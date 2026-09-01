import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from llm.backoff import LlmTransientError, with_backoff
from llm.client import LlmUnavailable
from llm.governor import Governor
from llm.models import MODEL_CHAIN, PRIMARY_MODEL
from llm.tools import TOOL_SCHEMAS, call_tool
from money.result import Err, Ok

ASK_MODEL = PRIMARY_MODEL
ASK_MODEL_CHAIN = MODEL_CHAIN
MAX_TOOL_HOPS = 2  # plus one final answer call => at most 3 requests per question

_NO_ANSWER = "I do not have that."
_UNGROUNDED = "I do not have that grounded in this run's data."
_NO_LOOKUPS = "I do not have that -- I couldn't find an answer within the allotted lookups."

SYSTEM_PROMPT_TEMPLATE = (
    "You are Lyra (Ledgerline's ask agent). Answer only using tool results -- never state a number that did not "
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


@dataclass(frozen=True)
class AskDelta:
    """A fragment of the model's answer as it is being written."""

    text: str


@dataclass(frozen=True)
class AskComplete:
    """The finished turn, yielded last by a streaming client."""

    turn: AskTurn


AskChunk = AskDelta | AskComplete


class AskClient(Protocol):
    async def next_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]], model: str
    ) -> AskTurn: ...

    def stream_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]], model: str
    ) -> AsyncIterator[AskChunk]: ...


@dataclass
class ScriptedAskClient:
    """A fixed sequence of turns played back in order, for deterministic
    tests -- the ask loop's conversation state is exercised for real, only
    the model's responses are canned."""

    turns: list[AskTurn]
    calls: int = field(default=0, init=False)

    async def next_turn(
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str = PRIMARY_MODEL,
    ) -> AskTurn:
        if self.calls >= len(self.turns):
            raise LlmUnavailable("ScriptedAskClient: no more scripted turns")
        turn = self.turns[self.calls]
        self.calls += 1
        return turn

    async def stream_turn(
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str = PRIMARY_MODEL,
    ) -> AsyncIterator[AskChunk]:
        """Replays a canned turn through the streaming shape: a text turn
        arrives word by word so the streaming path is exercised for real."""
        turn = await self.next_turn(system_prompt, history, tools, model)
        if turn.text:
            for index, word in enumerate(turn.text.split(" ")):
                yield AskDelta(text=word if index == 0 else " " + word)
        yield AskComplete(turn=turn)


class GeminiAskClient:
    """Talks to the real Gemini API with manual function-calling: automatic
    function calling is disabled so this module -- not the SDK -- controls
    the hop cap and can run the grounding check on the final answer.

    The model is chosen per call by the caller, which is what lets the ask
    loop fall through to the backup model when the primary refuses."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _request(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]]
    ) -> tuple[Any, Any]:
        """Build the (contents, config) pair shared by both call shapes."""
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
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=entry.tool_name, response=entry.tool_result
                                )
                            )
                        ],
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
            # The model may still reason internally; what it must not do is
            # hand that reasoning back as content. Nothing downstream wants
            # it: the user reads an answer, and the grounding check scores
            # the answer's numbers, not the working that led to them.
            thinking_config=types.ThinkingConfig(include_thoughts=False),
        )
        return contents, config

    @staticmethod
    def _is_thought(part: Any) -> bool:
        """True for a reasoning part. Belt and braces alongside
        include_thoughts=False: a provider that starts returning thoughts
        anyway must not leak them into an answer."""
        return bool(getattr(part, "thought", False))

    @staticmethod
    def _turn_from_part(part: Any, input_tokens: int, output_tokens: int) -> AskTurn:
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

    async def next_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]], model: str = ASK_MODEL
    ) -> AskTurn:
        from google import genai

        contents, config = self._request(system_prompt, history, tools)
        client = genai.Client(api_key=self._api_key)
        typed_contents: Any = contents
        try:
            response = await client.aio.models.generate_content(model=model, contents=typed_contents, config=config)
        except Exception as exc:
            raise LlmTransientError(str(exc)) from exc

        candidate = response.candidates[0] if response.candidates else None
        if candidate is None or candidate.content is None or not candidate.content.parts:
            raise LlmUnavailable("Gemini returned no content")

        # parts[0] can be a reasoning part, which would make the "answer"
        # the model's working; take the first part that is real content.
        content_parts = [p for p in candidate.content.parts if not self._is_thought(p)]
        if not content_parts:
            raise LlmUnavailable("Gemini returned only reasoning, no answer")

        usage = response.usage_metadata
        return self._turn_from_part(
            content_parts[0],
            usage.prompt_token_count if usage and usage.prompt_token_count else 0,
            usage.candidates_token_count if usage and usage.candidates_token_count else 0,
        )

    async def stream_turn(
        self, system_prompt: str, history: list[AskHistoryEntry], tools: list[dict[str, Any]], model: str = ASK_MODEL
    ) -> AsyncIterator[AskChunk]:
        """The same turn, delivered as it is written.

        Only the answer streams. Reasoning parts are dropped rather than
        forwarded, so the user reads what the model concluded and never
        watches it think; a function call has no text to stream and simply
        lands in the closing AskComplete. Usage counts and the Gemini-3
        thought signature are only final once the stream ends, so both are
        accumulated rather than read from the first chunk.
        """
        from google import genai

        contents, config = self._request(system_prompt, history, tools)
        client = genai.Client(api_key=self._api_key)
        typed_contents: Any = contents

        text_parts: list[str] = []
        function_call: Any = None
        thought_signature: Any = None
        input_tokens = 0
        output_tokens = 0

        try:
            stream = await client.aio.models.generate_content_stream(
                model=model, contents=typed_contents, config=config
            )
            async for chunk in stream:
                usage = getattr(chunk, "usage_metadata", None)
                if usage:
                    input_tokens = usage.prompt_token_count or input_tokens
                    output_tokens = usage.candidates_token_count or output_tokens

                candidate = chunk.candidates[0] if chunk.candidates else None
                if candidate is None or candidate.content is None or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    if self._is_thought(part):
                        continue  # reasoning is never shown and never kept
                    if getattr(part, "function_call", None) is not None:
                        function_call = part.function_call
                        thought_signature = getattr(part, "thought_signature", None)
                        continue
                    piece = getattr(part, "text", None)
                    if piece:
                        text_parts.append(piece)
                        yield AskDelta(text=piece)
        except Exception as exc:
            raise LlmTransientError(str(exc)) from exc

        if function_call is not None:
            if not function_call.name:
                raise LlmUnavailable("Gemini returned a function call with no name")
            yield AskComplete(
                turn=AskTurn(
                    tool_call=AskToolCall(name=function_call.name, args=dict(function_call.args or {})),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    thought_signature=thought_signature,
                )
            )
            return

        text = "".join(text_parts)
        if not text:
            raise LlmUnavailable("Gemini returned no content")
        yield AskComplete(turn=AskTurn(text=text, input_tokens=input_tokens, output_tokens=output_tokens))


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


async def _reserve_and_call(
    governor: Governor,
    user_id: str,
    models: Sequence[str],
    run: Callable[[str], Awaitable[AskTurn]],
) -> tuple[AskTurn | None, str | None]:
    """Try each model in the chain, in order.

    A model is skipped when the governor refuses it (its quota or rate limit
    is spent) and abandoned when the call itself fails after backoff -- both
    are reasons to fall through to the backup rather than to give up.
    """
    last_reason: str | None = None
    for model in models:
        reservation = await governor.check_and_reserve(model, user_id)
        if isinstance(reservation, Err):
            last_reason = f"{model}: {reservation.reason}"
            continue

        async def _attempt(name: str = model) -> AskTurn:
            return await run(name)

        try:
            return await with_backoff(_attempt, max_attempts=3), None
        except (LlmTransientError, LlmUnavailable) as exc:
            last_reason = f"{model}: {exc}"
            continue
    return None, last_reason


async def ask(
    question: str,
    run_id: str,
    user_id: str,
    db: AsyncSession,
    client: AskClient,
    governor: Governor,
    model: str | None = None,
    models: Sequence[str] = ASK_MODEL_CHAIN,
) -> AskAnswer:
    """The ask agent's tool loop: up to MAX_TOOL_HOPS tool calls, then one
    final answer call, capped at 3 total requests. Every tool call is
    dispatched through llm.tools.call_tool(), which re-checks tenancy against
    the real session user_id regardless of what run_id the model supplies --
    the model's belief about which run it's allowed to see is never trusted.

    `model` pins the chain to a single model; otherwise the primary is tried
    first and the backup picks up whatever it refuses.
    """
    chain = (model,) if model else tuple(models)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(run_id=run_id)
    history: list[AskHistoryEntry] = [AskHistoryEntry(role="user", text=question)]
    tool_payloads: list[dict[str, Any]] = []
    requests_issued = 0
    input_tokens = 0
    output_tokens = 0

    for _hop in range(MAX_TOOL_HOPS + 1):

        async def _run(model_name: str) -> AskTurn:
            return await client.next_turn(system_prompt, history, TOOL_SCHEMAS, model_name)

        turn, _reason = await _reserve_and_call(governor, user_id, chain, _run)
        if turn is None:
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
            return AskAnswer(_UNGROUNDED, False, requests_issued, input_tokens, output_tokens)
        return AskAnswer(answer_text, False, requests_issued, input_tokens, output_tokens)

    return AskAnswer(_NO_LOOKUPS, False, requests_issued, input_tokens, output_tokens)


async def ask_stream(
    question: str,
    run_id: str,
    user_id: str,
    db: AsyncSession,
    client: AskClient,
    governor: Governor,
    models: Sequence[str] = ASK_MODEL_CHAIN,
) -> AsyncIterator[dict[str, Any]]:
    """The same loop, surfaced as events so an answer can be read as it is
    written instead of arriving in one lump.

    The grounding guarantee is unchanged, and that is why a "done" event
    carries the answer rather than the client simply keeping what it saw: a
    stream that turns out to be ungrounded is replaced wholesale. Callers
    must render `done.answer`, not their accumulated deltas.

    Event shapes:
      {"type": "status", "state": "thinking"}
      {"type": "tool",   "name": "get_metrics"}
      {"type": "delta",  "text": "..."}          -- append to the draft
      {"type": "reset"}                          -- discard the draft
      {"type": "done",   "answer", "degraded", "grounded", "replaced"}
    """
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(run_id=run_id)
    history: list[AskHistoryEntry] = [AskHistoryEntry(role="user", text=question)]
    tool_payloads: list[dict[str, Any]] = []
    streamed = False

    for _hop in range(MAX_TOOL_HOPS + 1):
        yield {"type": "status", "state": "thinking"}

        turn: AskTurn | None = None
        for model in models:
            reservation = await governor.check_and_reserve(model, user_id)
            if isinstance(reservation, Err):
                continue
            drafted = False
            try:
                async for chunk in client.stream_turn(system_prompt, history, TOOL_SCHEMAS, model):
                    if isinstance(chunk, AskDelta):
                        drafted = True
                        streamed = True
                        yield {"type": "delta", "text": chunk.text}
                    else:
                        turn = chunk.turn
                break
            except (LlmTransientError, LlmUnavailable):
                # Anything already shown came from a model that then failed;
                # drop it before the backup starts writing its own answer.
                if drafted:
                    streamed = False
                    yield {"type": "reset"}
                turn = None
                continue

        if turn is None:
            yield {"type": "done", "answer": _NO_ANSWER, "degraded": True, "grounded": False, "replaced": streamed}
            return

        if turn.tool_call is not None:
            history.append(
                AskHistoryEntry(role="model", tool_call=turn.tool_call, thought_signature=turn.thought_signature)
            )
            result = await call_tool(turn.tool_call.name, turn.tool_call.args, db, user_id)
            payload = result.value if isinstance(result, Ok) else {"error": result.reason}
            tool_payloads.append(payload)
            history.append(AskHistoryEntry(role="tool", tool_name=turn.tool_call.name, tool_result=payload))
            yield {"type": "tool", "name": turn.tool_call.name}
            continue

        answer_text = turn.text or ""
        if not answer_text or not _is_grounded(answer_text, tool_payloads):
            yield {
                "type": "done",
                "answer": _UNGROUNDED,
                "degraded": False,
                "grounded": False,
                "replaced": True,
            }
            return
        yield {"type": "done", "answer": answer_text, "degraded": False, "grounded": True, "replaced": False}
        return

    yield {"type": "done", "answer": _NO_LOOKUPS, "degraded": False, "grounded": False, "replaced": streamed}
