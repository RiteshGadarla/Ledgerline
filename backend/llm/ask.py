import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from llm.backoff import LlmTransientError, with_backoff
from llm.client import LlmUnavailable
from llm.governor import Governor
from llm.tools import TOOL_SCHEMAS, call_tool
from money.result import Err, Ok

ASK_MODEL = "gemini-3.6-flash"
MAX_TOOL_HOPS = 2  # plus one final answer call => at most 3 requests per question, per the Gemini budget table

_NO_ANSWER = "I do not have that."

SYSTEM_PROMPT_TEMPLATE = (
    "You are Ledgerline's ask agent. Answer only using tool results -- never state a number that did not "
    "come from a tool result, and cite the record ids a claim is about. If the tools don't give you enough "
    "to answer, say plainly that you do not have that information rather than guessing. "
    "The user is currently looking at run {run_id}; use this run_id in tool calls unless the question "
    "names a different one."
)


@dataclass(frozen=True)
class AskToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class AskTurn:
    """Exactly one of tool_call / text is set, mirroring a single model turn.

    thought_signature is an opaque, Gemini-3-specific continuation token
    that rides alongside a function-call Part: the API rejects a follow-up
    turn if a prior function call's signature isn't echoed back verbatim in
    history, so it has to be carried even though nothing here inspects it.
    """

    tool_call: AskToolCall | None = None
    text: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    thought_signature: Any | None = None


@dataclass(frozen=True)
class AskHistoryEntry:
    role: Literal["user", "model", "tool"]
    text: str | None = None
    tool_call: AskToolCall | None = None
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    thought_signature: Any | None = None


class AskClient(Protocol):
    async def next_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]]
    ) -> AskTurn: ...


@dataclass
class ScriptedAskClient:
    """A fixed sequence of turns played back in order, for deterministic
    tests -- the ask loop's conversation state is exercised for real, only
    the model's responses are canned."""

    turns: list[AskTurn]
    calls: int = field(default=0, init=False)

    async def next_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]]
    ) -> AskTurn:
        if self.calls >= len(self.turns):
            raise LlmUnavailable("ScriptedAskClient: no more scripted turns")
        turn = self.turns[self.calls]
        self.calls += 1
        return turn


class GeminiAskClient:
    """Talks to the real Gemini API with manual function-calling: automatic
    function calling is disabled so this module -- not the SDK -- controls
    the hop cap and can run the grounding check on the final answer."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def next_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]]
    ) -> AskTurn:
        from google import genai
        from google.genai import types

        contents = []
        for entry in history:
            if entry.role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=entry.text)]))
            elif entry.role == "model":
                if entry.tool_call is not None:
                    part = types.Part(
                        function_call=types.FunctionCall(name=entry.tool_call.name, args=entry.tool_call.args),
                        thought_signature=entry.thought_signature,
                    )
                else:
                    part = types.Part(text=entry.text)
                contents.append(types.Content(role="model", parts=[part]))
            else:  # role == "tool"
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(function_response=types.FunctionResponse(
                            name=entry.tool_name, response=entry.tool_result
                        ))],
                    )
                )

        declarations = [
            types.FunctionDeclaration(
                name=t["name"], description=t["description"], parameters_json_schema=t["parameters"]
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        client = genai.Client(api_key=self._api_key)
        # The SDK's `contents` union doesn't spell out list[Content] cleanly.
        typed_contents: Any = contents
        try:
            response = await client.aio.models.generate_content(
                model=ASK_MODEL, contents=typed_contents, config=config
            )
        except Exception as exc:
            raise LlmTransientError(str(exc)) from exc

        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None or not candidate.content.parts:
            raise LlmUnavailable("Gemini returned no content")

        part = candidate.content.parts[0]
        usage = response.usage_metadata
        input_tokens = usage.prompt_token_count if usage and usage.prompt_token_count else 0
        output_tokens = usage.candidates_token_count if usage and usage.candidates_token_count else 0

        if part.function_call is not None:
            if not part.function_call.name:
                raise LlmUnavailable("Gemini returned a function call with no name")
            return AskTurn(
                tool_call=AskToolCall(name=part.function_call.name, args=dict(part.function_call.args or {})),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                thought_signature=part.thought_signature,
            )
        return AskTurn(text=part.text or "", input_tokens=input_tokens, output_tokens=output_tokens)


@dataclass(frozen=True)
class AskAnswer:
    text: str
    degraded: bool
    requests_issued: int
    input_tokens: int
    output_tokens: int


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_-])\d[\d,]*\.?\d*(?![A-Za-z0-9_-])")
_LIST_MARKER_RE = re.compile(r"(?m)^\s*\d+\.\s+")


def _numbers_in_text(text: str) -> list[float]:
    # Strip markdown ordered-list markers ("1. ", "16. ") first: a list
    # position is not a claim about the data and would otherwise need to
    # coincidentally match a real tool-result number to avoid a false
    # grounding failure. Everything else on the line is still scanned.
    text = _LIST_MARKER_RE.sub("", text)
    numbers = []
    for match in _NUMBER_RE.findall(text):
        cleaned = match.replace(",", "")
        if cleaned in ("", "-", "."):
            continue
        try:
            numbers.append(float(cleaned))
        except ValueError:
            continue
    return numbers


def _numbers_in_json(value: Any) -> set[float]:
    numbers: set[float] = set()
    if isinstance(value, bool):
        return numbers
    if isinstance(value, int | float):
        numbers.add(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            numbers |= _numbers_in_json(v)
    elif isinstance(value, list):
        for v in value:
            numbers |= _numbers_in_json(v)
    return numbers


def _is_grounded(answer_text: str, tool_payloads: list[dict[str, Any]]) -> bool:
    """Every number in the answer must trace to a tool result -- as itself,
    as a percentage of a 0-1 fraction (0.733 -> "73.3%"), or as a rounding of
    either, since a faithful restatement of a real number should never be
    flagged as fabricated just because it was reformatted for prose."""
    known: set[float] = set()
    for payload in tool_payloads:
        known |= _numbers_in_json(payload)
    candidates = known | {round(n * 100, 6) for n in known} | {round(n / 100, 6) for n in known}

    for number in _numbers_in_text(answer_text):
        if not any(abs(number - c) < 0.05 or (c != 0 and abs(number - c) / abs(c) < 0.01) for c in candidates):
            return False
    return True


async def ask(
    question: str, run_id: str, user_id: str, db: AsyncSession, client: AskClient, governor: Governor
) -> AskAnswer:
    """The ask agent's tool loop: up to MAX_TOOL_HOPS tool calls, then one
    final answer call, capped at 3 total requests. Every tool call is
    dispatched through llm.tools.call_tool(), which re-checks tenancy against
    the real session user_id regardless of what run_id the model supplies --
    the model's belief about which run it's allowed to see is never trusted.
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(run_id=run_id)
    history: list[AskHistoryEntry] = [AskHistoryEntry(role="user", text=question)]
    tool_payloads: list[dict[str, Any]] = []
    requests_issued = 0
    input_tokens = 0
    output_tokens = 0

    for _hop in range(MAX_TOOL_HOPS + 1):
        reservation = await governor.check_and_reserve(ASK_MODEL, user_id)
        if isinstance(reservation, Err):
            return AskAnswer(_NO_ANSWER, True, requests_issued, input_tokens, output_tokens)

        async def _call() -> AskTurn:
            return await client.next_turn(system_prompt, history, TOOL_SCHEMAS)

        try:
            turn = await with_backoff(_call, max_attempts=3)
        except (LlmTransientError, LlmUnavailable):
            return AskAnswer(_NO_ANSWER, True, requests_issued, input_tokens, output_tokens)

        requests_issued += 1
        input_tokens += turn.input_tokens
        output_tokens += turn.output_tokens

        if turn.tool_call is not None:
            history.append(
                AskHistoryEntry(role="model", tool_call=turn.tool_call, thought_signature=turn.thought_signature)
            )
            result = await call_tool(turn.tool_call.name, turn.tool_call.args, db, user_id)
            payload = result.value if isinstance(result, Ok) else {"error": result.reason}
            tool_payloads.append(payload)
            history.append(AskHistoryEntry(role="tool", tool_name=turn.tool_call.name, tool_result=payload))
            continue

        answer_text = turn.text or ""
        if not answer_text or not _is_grounded(answer_text, tool_payloads):
            return AskAnswer(
                "I do not have that grounded in this run's data.", False, requests_issued, input_tokens, output_tokens
            )
        return AskAnswer(answer_text, False, requests_issued, input_tokens, output_tokens)

    return AskAnswer(
        "I do not have that -- I couldn't find an answer within the allotted lookups.",
        False,
        requests_issued,
        input_tokens,
        output_tokens,
    )
