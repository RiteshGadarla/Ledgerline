from dataclasses import dataclass

from contracts.enums import ExceptionCode, PassId
from contracts.models import Evidence, Exception_, MatchGroup, RejectedProposal
from engine.exceptions import build_exception
from engine.index import CorpusIndex
from engine.passes import p3_invoice_to_payment
from engine.verifier import MatchProposal, UsedRecordIds, verify
from llm.client import LlmResponse
from llm.gateway import LlmGateway
from llm.schemas import TRIAGE_SCHEMA_VERSION, LlmMatchProposal, TriageResponse
from money.result import Err, Ok, Result

TRIAGE_MODEL = "gemini-2.5-flash"


@dataclass(frozen=True)
class TriageCandidate:
    settlement_id: str
    bank_line_ids: list[str]


@dataclass(frozen=True)
class TriageOutcome:
    groups: list[MatchGroup]
    exceptions: list[Exception_]
    used: UsedRecordIds
    requests_issued: int
    tokens_used: int


def build_candidates(
    settlement_ids: list[str], index: CorpusIndex, unresolved_bank_line_ids: set[str]
) -> list[TriageCandidate]:
    """A settlement is only offered candidates whose amount already ties out;
    the model's job is to pick the right one from narration context, not to
    invent an amount match itself."""
    candidates = []
    for settlement_id in sorted(settlement_ids):
        settlement = index.settlements_by_id[settlement_id]
        matching_bank_lines = sorted(
            bank_line_id
            for bank_line_id in unresolved_bank_line_ids
            if index.bank_lines_by_id[bank_line_id].credit == settlement.payout
        )
        if matching_bank_lines:
            candidates.append(TriageCandidate(settlement_id, matching_bank_lines))
    return candidates


def source_text(candidate: TriageCandidate, index: CorpusIndex) -> str:
    """The only text a proposal's evidence_spans may quote from."""
    return "\n".join(index.bank_lines_by_id[bid].narration for bid in candidate.bank_line_ids)


def build_prompt(candidate: TriageCandidate, index: CorpusIndex) -> str:
    lines = [
        f"Settlement {candidate.settlement_id} needs its bank credit identified.",
        "Candidates (bank_line_id: narration):",
    ]
    for bank_line_id in candidate.bank_line_ids:
        lines.append(f"{bank_line_id}: {index.bank_lines_by_id[bank_line_id].narration}")
    lines.append(
        "Return JSON matching the schema. Pick the correct bank_line_id from the candidates"
        " listed above and quote evidence_spans verbatim from its narration."
    )
    return "\n".join(lines)


def _rejected_exception(
    settlement_id: str, index: CorpusIndex, proposal: LlmMatchProposal, failed_check: str
) -> Exception_:
    base = build_exception(
        "settlement", settlement_id, ExceptionCode.LLM_PROPOSAL_FAILED_VERIFY, index, attempted=["P1", "LLM"]
    )
    rejected = RejectedProposal(
        proposed_by="llm",
        match_group={"bank_line_id": proposal.bank_line_id, "evidence_spans": proposal.evidence_spans},
        failed_check=failed_check,
    )
    return base.model_copy(update={"rejected_proposal": rejected})


def resolve_candidate(
    candidate: TriageCandidate,
    response: TriageResponse,
    index: CorpusIndex,
    used: UsedRecordIds,
) -> tuple[list[MatchGroup], list[Exception_], UsedRecordIds]:
    """Grounding, then verification -- in that order. Neither step trusts the
    model: a proposal naming a bank line we never offered as a candidate for
    THIS settlement is rejected before its evidence is even checked, and a span
    that isn't a verbatim substring of the narration we showed is rejected
    before verify() ever runs. verify() remains the only path that writes a
    match; a well-grounded proposal that doesn't tie out in ints is still
    rejected there, exactly like a deterministic-pass proposal would be."""
    groups: list[MatchGroup] = []
    exceptions: list[Exception_] = []
    corpus_text = source_text(candidate, index)

    for proposal in response.proposals:
        if proposal.bank_line_id not in candidate.bank_line_ids:
            exceptions.append(
                _rejected_exception(
                    candidate.settlement_id, index, proposal, "proposed bank_line_id was not an offered candidate"
                )
            )
            continue

        ungrounded = [span for span in proposal.evidence_spans if span not in corpus_text]
        if ungrounded:
            reason = f"ungrounded evidence span: {ungrounded[0]!r}"
            exceptions.append(_rejected_exception(candidate.settlement_id, index, proposal, reason))
            continue

        settlement = index.settlements_by_id[candidate.settlement_id]
        invoice_ids: list[str] = []
        for payment_id in settlement.payment_ids:
            resolved = p3_invoice_to_payment(index, payment_id)
            if resolved is not None:
                invoice_ids.append(resolved[0])

        match_proposal = MatchProposal(
            invoice_ids=invoice_ids,
            payment_ids=list(settlement.payment_ids),
            settlement_id=candidate.settlement_id,
            bank_line_id=proposal.bank_line_id,
            pass_id=PassId.LLM,
            confidence=proposal.confidence,
            evidence=[
                Evidence(field="narration", value=span, source_id=proposal.bank_line_id)
                for span in proposal.evidence_spans
            ],
        )
        outcome = verify(match_proposal, index, used)
        if isinstance(outcome, Ok):
            group = outcome.value.model_copy(update={"status": "assisted"})
            groups.append(group)
            used = used.with_group(group)
        else:
            exceptions.append(_rejected_exception(candidate.settlement_id, index, proposal, outcome.reason))

    return groups, exceptions, used


async def run_triage(
    settlement_ids: list[str],
    index: CorpusIndex,
    unresolved_bank_line_ids: set[str],
    gateway: LlmGateway,
    used: UsedRecordIds,
    user_id: str,
) -> TriageOutcome:
    groups: list[MatchGroup] = []
    exceptions: list[Exception_] = []
    requests_issued = 0
    tokens_used = 0

    for candidate in build_candidates(settlement_ids, index, unresolved_bank_line_ids):
        prompt = build_prompt(candidate, index)
        result: Result[LlmResponse] = await gateway.generate(
            model=TRIAGE_MODEL, prompt=prompt, response_schema=TriageResponse, user_id=user_id
        )
        if isinstance(result, Err):
            continue  # degraded: caller already has this settlement's base exception

        requests_issued += 1
        tokens_used += result.value.input_tokens + result.value.output_tokens
        response = TriageResponse.model_validate_json(result.value.raw_json)

        new_groups, new_exceptions, used = resolve_candidate(candidate, response, index, used)
        groups.extend(new_groups)
        exceptions.extend(new_exceptions)

    return TriageOutcome(groups, exceptions, used, requests_issued, tokens_used)


__all__ = [
    "TRIAGE_SCHEMA_VERSION",
    "TriageCandidate",
    "TriageOutcome",
    "build_candidates",
    "resolve_candidate",
    "run_triage",
]
