import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from llm.backoff import LlmTransientError, with_backoff
from llm.client import LlmUnavailable
from llm.governor import Governor
from llm.keys import ApiKey, KeyPool
from llm.models import MODEL_CHAIN, PRIMARY_MODEL
from llm.tools import TOOL_SCHEMAS, call_tool
from money.result import Err, Ok

ASK_MODEL = PRIMARY_MODEL
ASK_MODEL_CHAIN = MODEL_CHAIN
# Six lookups plus a final answer call. Two was enough for "what is the auto
# rate" and nothing else: a question like "why is this run worse than the last
# one" needs list_runs, compare_runs and summarise_exceptions before a word can
# be written, and used to die on the hop cap with "I couldn't find an answer
# within the allotted lookups". The governor, not this number, is what stops a
# runaway conversation from spending quota.
MAX_TOOL_HOPS = 6

# How many prior turns of the conversation are replayed to the model. Enough
# for "and the biggest one?" to resolve against what was just discussed,
# bounded because every turn is re-sent as input tokens on each hop.
MAX_HISTORY_TURNS = 12

_NO_ANSWER = "I do not have that."
_UNGROUNDED = "I do not have that grounded in this run's data."
_NO_LOOKUPS = "I do not have that -- I couldn't find an answer within the allotted lookups."

SYSTEM_PROMPT_TEMPLATE = (
    "You are Lyra, the reconciliation analyst inside Ledgerline. You answer questions about finance runs "
    "for the person who is looking at one.\n"
    "\n"
    "GROUNDING (absolute):\n"
    "- Every number you state must have come from a tool result in this conversation. Never estimate, "
    "never total figures yourself when a tool reports the total, and never carry a number over from your "
    "own general knowledge.\n"
    "- An answer containing a number that no tool returned is discarded before the user sees it, so a "
    "guess costs you the whole answer. If the tools do not cover it, say plainly what you do not have.\n"
    "- Cite the record and run ids a claim is about, written out in full so they can be linked.\n"
    "\n"
    "HOW TO WORK:\n"
    "- Look things up before answering. You may call several tools in sequence, and using one more tool is "
    "always better than reasoning past a gap.\n"
    "- Prefer the tool that answers the question directly: summarise_exceptions for 'what is broken', "
    "compare_runs for any difference between two runs, search_records when the question names a UTR, a "
    "narration or part of an id, get_dataset for what the run was reading.\n"
    "- Row lists are capped. Answer 'how many' from the reported total, never by counting rows.\n"
    "- Check get_decisions before recommending an action, so you do not raise something already dealt with.\n"
    "\n"
    "HOW TO WRITE:\n"
    "- Lead with the answer in one sentence, then the detail that supports it. Short markdown only: a "
    "sentence or two, or a tight list. No headings, no preamble, no restating the question.\n"
    "- Rupee figures come back in paise; present them as rupees and say so naturally.\n"
    "- Be direct about bad news. An open exception is a problem to name, not to soften.\n"
    "\n"
    "CONTEXT: the user is looking at run {run_id}. Use that run_id unless the question names another run "
    "or asks across runs."
)

# The same agent, for a user who is not on a run surface. It has no run to
# default to, so its first move is almost always list_runs -- which is the
# honest shape of "which run went best?" rather than a guess at which one they
# meant.
GLOBAL_SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE.replace(
    "CONTEXT: the user is looking at run {run_id}. Use that run_id unless the question names another run "
    "or asks across runs.",
    "CONTEXT: the user is not looking at any particular run. Call list_runs first to find the run they "
    "mean; if the question is about 'the last run' or 'my latest run', that is the newest one it returns.",
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
    """`api_key` is the credential the governor reserved this turn against;
    a client with nothing to spend (the scripted double) ignores it."""

    async def next_turn(
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str,
        api_key: ApiKey | None = None,
    ) -> AskTurn: ...

    def stream_turn(
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str,
        api_key: ApiKey | None = None,
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
        api_key: ApiKey | None = None,
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
        api_key: ApiKey | None = None,
    ) -> AsyncIterator[AskChunk]:
        """Replays a canned turn through the streaming shape: a text turn
        arrives word by word so the streaming path is exercised for real."""
        turn = await self.next_turn(system_prompt, history, tools, model, api_key)
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

    def __init__(self, keys: KeyPool) -> None:
        self._keys = keys

    def _sdk_client(self, api_key: ApiKey | None) -> Any:
        """Bind the SDK to the credential the governor reserved, falling back
        to the pool's own rotation for a caller with no governor in front."""
        from google import genai

        key = api_key or self._keys.next_key()
        if key is None or not key.value:
            raise LlmUnavailable("no Gemini API key configured")
        return genai.Client(api_key=key.value)

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
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str = ASK_MODEL,
        api_key: ApiKey | None = None,
    ) -> AskTurn:
        contents, config = self._request(system_prompt, history, tools)
        client = self._sdk_client(api_key)
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
        self,
        system_prompt: str,
        history: list[AskHistoryEntry],
        tools: list[dict[str, Any]],
        model: str = ASK_MODEL,
        api_key: ApiKey | None = None,
    ) -> AsyncIterator[AskChunk]:
        """The same turn, delivered as it is written.

        Only the answer streams. Reasoning parts are dropped rather than
        forwarded, so the user reads what the model concluded and never
        watches it think; a function call has no text to stream and simply
        lands in the closing AskComplete. Usage counts and the Gemini-3
        thought signature are only final once the stream ends, so both are
        accumulated rather than read from the first chunk.
        """
        contents, config = self._request(system_prompt, history, tools)
        client = self._sdk_client(api_key)
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
    run: Callable[[str, ApiKey], Awaitable[AskTurn]],
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

        # The call must use the credential the reservation was made against,
        # or the per-key counters stop describing what was actually spent.
        async def _attempt(name: str = model, key: ApiKey = reservation.value) -> AskTurn:
            return await run(name, key)

        try:
            return await with_backoff(_attempt, max_attempts=3), None
        except (LlmTransientError, LlmUnavailable) as exc:
            last_reason = f"{model}: {exc}"
            continue
    return None, last_reason



def build_system_prompt(run_id: str | None) -> str:
    """Run-scoped or account-wide, depending on where the question came from."""
    return GLOBAL_SYSTEM_PROMPT if not run_id else SYSTEM_PROMPT_TEMPLATE.format(run_id=run_id)


def seed_history(question: str, prior: Sequence[tuple[str, str]] = ()) -> list[AskHistoryEntry]:
    """The conversation so far, then the new question.

    Prior turns arrive from the client rather than a server-side store: the
    transcript is already in the browser, the agent holds no state between
    requests, and a user can only ever replay their own conversation to
    themselves. Nothing here is trusted as fact -- it is dialogue context, and
    every number in the final answer must still come from a tool result on
    *this* turn, which the grounding check enforces regardless of what the
    history claims.
    """
    entries: list[AskHistoryEntry] = []
    for role, text in list(prior)[-MAX_HISTORY_TURNS:]:
        if not text:
            continue
        entries.append(AskHistoryEntry(role="model" if role in ("lyra", "model") else "user", text=text))
    entries.append(AskHistoryEntry(role="user", text=question))
    return entries


# Where an id found in an answer should link to, by the tool-payload key it
# was collected from.
_ID_KINDS = (
    ("invoice_ids", "invoice"),
    ("payment_ids", "payment"),
    ("settlement_id", "settlement"),
    ("bank_line_id", "bank_line"),
)


def _collect_ids(value: Any, found: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, kind in _ID_KINDS:
            item = value.get(key)
            if isinstance(item, str):
                found[item] = kind
            elif isinstance(item, list):
                for entry in item:
                    if isinstance(entry, str):
                        found[entry] = kind
        # A RecordRef carries its own kind, which beats inferring one.
        if isinstance(value.get("kind"), str) and isinstance(value.get("id"), str):
            found[value["id"]] = value["kind"]
        if isinstance(value.get("run_id"), str):
            found[value["run_id"]] = "run"
        for nested in value.values():
            _collect_ids(nested, found)
    elif isinstance(value, list):
        for nested in value:
            _collect_ids(nested, found)


def citations(answer_text: str, tool_payloads: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The record ids the answer actually mentions, with what each one is.

    Collected from tool results rather than parsed out of the prose, so a
    citation is by construction something a tool returned -- the same standard
    the numbers are held to. An id the model invented appears in no payload
    and therefore gets no chip.
    """
    known: dict[str, str] = {}
    for payload in tool_payloads:
        _collect_ids(payload, known)

    cited = [
        {"id": record_id, "kind": kind}
        for record_id, kind in known.items()
        if record_id and len(record_id) > 3 and record_id in answer_text
    ]
    cited.sort(key=lambda c: answer_text.index(c["id"]))
    return cited[:12]


# What to offer next, by the tool that was just used. Derived rather than
# generated: a follow-up suggestion is navigation, and spending a model call
# (and a slice of the user's daily quota) to write three of them would be the
# most expensive furniture on the page.
_FOLLOW_UPS: dict[str, tuple[str, ...]] = {
    "get_metrics": ("What is sitting in exceptions?", "How does this compare to my last run?"),
    "summarise_exceptions": ("Which single exception has the most money behind it?", "What should I action first?"),
    "query_exceptions": ("What would clearing the largest one be worth?", "Has anyone decided on these already?"),
    "query_matches": ("Which of these needed the model's help?", "Show me one chain end to end."),
    "get_record": ("What else is in that chain?", "Why did this one not tie out?"),
    "get_forecast": ("What is blocking the largest day?", "How much of this is at risk?"),
    "list_runs": ("Compare my two most recent runs.", "Which run had the fewest exceptions?"),
    "compare_runs": ("What changed in the exceptions?", "Was the difference the dataset or the engine?"),
    "search_records": ("What happened to that chain?", "Are there others like it?"),
    "get_dataset": ("Can these figures be scored for accuracy?", "What is the match rate on this corpus?"),
    "get_decisions": ("What is still open?", "What should I action first?"),
}

_DEFAULT_FOLLOW_UPS = ("What is sitting in exceptions?", "Where does the cash land?")


def follow_ups(tools_used: Sequence[str]) -> list[str]:
    """Two or three next questions, keyed to what was just looked at."""
    suggestions: list[str] = []
    for tool in reversed(list(tools_used)):
        for candidate in _FOLLOW_UPS.get(tool, ()):
            if candidate not in suggestions:
                suggestions.append(candidate)
        if len(suggestions) >= 3:
            break
    for candidate in _DEFAULT_FOLLOW_UPS:
        if len(suggestions) >= 3:
            break
        if candidate not in suggestions:
            suggestions.append(candidate)
    return suggestions[:3]


# What each tool is doing, in words a person reads while they wait. The panel
# shows this instead of a spinner, which is the difference between "it is
# thinking" and "it is reading my exception list".
TOOL_LABELS: dict[str, str] = {
    "get_metrics": "Reading the scoreboard",
    "query_matches": "Reading matched chains",
    "query_exceptions": "Reading open exceptions",
    "get_record": "Looking up a record",
    "get_forecast": "Reading the cash forecast",
    "list_runs": "Finding your runs",
    "compare_runs": "Comparing two runs",
    "search_records": "Searching this run",
    "get_dataset": "Checking the dataset",
    "summarise_exceptions": "Grouping exceptions by cause",
    "get_decisions": "Checking recorded decisions",
}


def tool_label(name: str, args: dict[str, Any] | None = None) -> str:
    label = TOOL_LABELS.get(name, f"Calling {name}")
    if name == "search_records" and args and isinstance(args.get("text"), str):
        return f"Searching this run for {args['text']!r}"
    return label


async def ask(
    question: str,
    run_id: str | None,
    user_id: str,
    db: AsyncSession,
    client: AskClient,
    governor: Governor,
    model: str | None = None,
    models: Sequence[str] = ASK_MODEL_CHAIN,
    prior: Sequence[tuple[str, str]] = (),
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
    system_prompt = build_system_prompt(run_id)
    history = seed_history(question, prior)
    tool_payloads: list[dict[str, Any]] = []
    requests_issued = 0
    input_tokens = 0
    output_tokens = 0

    for _hop in range(MAX_TOOL_HOPS + 1):

        async def _run(model_name: str, api_key: ApiKey) -> AskTurn:
            return await client.next_turn(system_prompt, history, TOOL_SCHEMAS, model_name, api_key)

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
    run_id: str | None,
    user_id: str,
    db: AsyncSession,
    client: AskClient,
    governor: Governor,
    models: Sequence[str] = ASK_MODEL_CHAIN,
    prior: Sequence[tuple[str, str]] = (),
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
    system_prompt = build_system_prompt(run_id)
    history = seed_history(question, prior)
    tool_payloads: list[dict[str, Any]] = []
    tools_used: list[str] = []
    input_tokens = 0
    output_tokens = 0
    requests_issued = 0
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
                async for chunk in client.stream_turn(system_prompt, history, TOOL_SCHEMAS, model, reservation.value):
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
            yield {
                "type": "done",
                "answer": _NO_ANSWER,
                "degraded": True,
                "grounded": False,
                "replaced": streamed,
                "citations": [],
                "follow_ups": [],
                "usage": {
                    "requests": requests_issued,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "tools": tools_used,
                },
            }
            return

        requests_issued += 1
        input_tokens += turn.input_tokens
        output_tokens += turn.output_tokens

        if turn.tool_call is not None:
            history.append(
                AskHistoryEntry(role="model", tool_call=turn.tool_call, thought_signature=turn.thought_signature)
            )
            # Announced before the lookup runs, not after: the label is there
            # to tell someone what is happening during the wait, and a wait
            # that is already over needs no explanation.
            yield {
                "type": "tool",
                "name": turn.tool_call.name,
                "label": tool_label(turn.tool_call.name, turn.tool_call.args),
            }
            result = await call_tool(turn.tool_call.name, turn.tool_call.args, db, user_id)
            payload = result.value if isinstance(result, Ok) else {"error": result.reason}
            tool_payloads.append(payload)
            tools_used.append(turn.tool_call.name)
            history.append(AskHistoryEntry(role="tool", tool_name=turn.tool_call.name, tool_result=payload))
            continue

        usage = {
            "requests": requests_issued,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tools": tools_used,
        }

        answer_text = turn.text or ""
        if not answer_text or not _is_grounded(answer_text, tool_payloads):
            yield {
                "type": "done",
                "answer": _UNGROUNDED,
                "degraded": False,
                "grounded": False,
                "replaced": True,
                "citations": [],
                "follow_ups": follow_ups(tools_used),
                "usage": usage,
            }
            return
        yield {
            "type": "done",
            "answer": answer_text,
            "degraded": False,
            "grounded": True,
            "replaced": False,
            "citations": citations(answer_text, tool_payloads),
            "follow_ups": follow_ups(tools_used),
            "usage": usage,
        }
        return

    yield {
        "type": "done",
        "answer": _NO_LOOKUPS,
        "degraded": False,
        "grounded": False,
        "replaced": streamed,
        "citations": [],
        "follow_ups": follow_ups(tools_used),
        "usage": {
            "requests": requests_issued,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tools": tools_used,
        },
    }
