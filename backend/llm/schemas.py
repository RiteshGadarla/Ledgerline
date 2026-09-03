from pydantic import BaseModel

TRIAGE_SCHEMA_VERSION = "triage-v1"
EXPLAIN_SCHEMA_VERSION = "explain-v2"

_EXPLANATION_MAX_CHARS = 240


class LlmMatchProposal(BaseModel):
    """Deliberately carries no settlement_id: the caller always verifies against
    the settlement it explicitly asked about, never whatever the model claims,
    so an attempt to inject a target elsewhere in the payload has nothing to act
    on."""

    bank_line_id: str
    confidence: float
    evidence_spans: list[str]


class TriageResponse(BaseModel):
    proposals: list[LlmMatchProposal]


class ExplanationItem(BaseModel):
    """Keyed by exception *code*, not by exception id.

    An explanation is a property of the kind of break, not of the individual
    record that broke: an observed run held 88 exceptions across 4 codes and
    the model returned the same 4 sentences 88 times. Asking once per code
    and fanning the answer out is the same output for a fraction of the
    tokens -- which, since a model emits those tokens serially, is the
    difference between the stage taking two minutes and taking seconds.
    """

    code: str
    explanation: str
    suggested_action: str


class ExplanationResponse(BaseModel):
    items: list[ExplanationItem]


def cap_explanation(text: str) -> str:
    return text if len(text) <= _EXPLANATION_MAX_CHARS else text[: _EXPLANATION_MAX_CHARS - 1] + "…"
