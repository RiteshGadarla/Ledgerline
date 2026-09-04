import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from contracts.corpus import Corpus
from contracts.models import RunMetrics
from datagen.models import Truth
from engine.index import build_index
from engine.pipeline import MatchResult, hash_groups, match
from engine.verifier import UsedRecordIds
from llm.explain import explain
from llm.gateway import LlmGateway
from llm.triage import run_triage
from scripts.eval import score_run

RunState = str  # "normalising" | "matching" | "triaging" | "explaining" | "scoring"

PublishState = Callable[[RunState], Awaitable[None]]


@dataclass(frozen=True)
class PipelineOutcome:
    result: MatchResult
    metrics: RunMetrics


async def _noop_publish(_state: RunState) -> None:
    return None


async def run_pipeline(
    corpus: Corpus,
    truth: Truth | None,
    gateway: LlmGateway,
    user_id: str,
    publish_state: PublishState = _noop_publish,
) -> PipelineOutcome:
    """The full reconciliation pipeline a run walks through: deterministic
    matching, then LLM-assisted triage on the residue, then explanations for
    whatever is still open. This is the single place worker jobs and tests
    both call, so the state machine's phases and the LLM budget accounting
    can never drift between the two.

    Normalisation happens before this function is ever called -- for a demo
    corpus that's a no-op (already canonical), and for an uploaded dataset
    it's the ingest/ pipeline. The "normalising" state is still published for
    a demo run so the state machine's shape stays uniform regardless of source.
    """
    start = time.perf_counter()

    await publish_state("normalising")
    await publish_state("matching")
    result = match(corpus)
    index = build_index(corpus)

    exceptioned_settlement_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "settlement"}
    unresolved_bank_line_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "bank_line"}
    used = UsedRecordIds()
    for group in result.groups:
        used = used.with_group(group)

    await publish_state("triaging")
    triage_outcome = await run_triage(
        list(exceptioned_settlement_ids), index, unresolved_bank_line_ids, gateway, used, user_id
    )
    # Every record an assisted group closed, not just the settlement that was
    # sent to triage. A settlement arriving at triage brings its payments, its
    # invoices and its bank line with it, and each of those was filed as its
    # own exception during the deterministic pass -- dropping only the
    # settlement's would leave the rest of the group sitting in the open list
    # while the same records are also counted as assisted, inflating the
    # exception count, the rupees at risk and the open rate all at once.
    resolved = UsedRecordIds()
    for group in triage_outcome.groups:
        resolved = resolved.with_group(group)
    resolved_ids_by_kind = {
        "settlement": resolved.settlement_ids,
        "payment": resolved.payment_ids,
        "invoice": resolved.invoice_ids,
        "bank_line": resolved.bank_line_ids,
    }
    remaining_exceptions = [
        exc
        for exc in result.exceptions
        if not any(r.id in resolved_ids_by_kind.get(r.kind, frozenset()) for r in exc.records)
    ]
    remaining_exceptions.extend(triage_outcome.exceptions)
    all_groups = result.groups + triage_outcome.groups

    await publish_state("explaining")
    annotated_exceptions, explain_in_tokens, explain_out_tokens, explain_degraded = await explain(
        remaining_exceptions, gateway, user_id
    )

    await publish_state("scoring")
    combined_result = MatchResult(
        groups=all_groups, exceptions=annotated_exceptions, output_hash=hash_groups(all_groups)
    )
    elapsed_seconds = time.perf_counter() - start
    metrics = score_run(corpus, combined_result, truth, elapsed_seconds)
    metrics = metrics.model_copy(
        update={
            # `explain` issues exactly one request, and only when it had
            # something to explain and got an answer back. Counting it
            # whenever there were exceptions credited the run with a request
            # that a degraded stage never made.
            "llm_requests": triage_outcome.requests_issued
            + (1 if remaining_exceptions and not explain_degraded else 0),
            "llm_tokens": triage_outcome.tokens_used + explain_in_tokens + explain_out_tokens,
            "llm_degraded": triage_outcome.degraded or explain_degraded,
        }
    )
    return PipelineOutcome(result=combined_result, metrics=metrics)
