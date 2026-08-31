from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from contracts.enums import ExceptionCode, PassId
from contracts.money import Paise


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Invoice(Frozen):
    id: str
    number: str
    customer: str
    amount: Paise
    issued_at: date
    ref: str | None = None


class Payment(Frozen):
    id: str
    order_id: str | None = None
    invoice_ref: str | None = None
    gross: Paise
    fee: Paise
    tax: Paise
    net: Paise
    status: Literal["captured", "refunded", "failed", "disputed"]
    captured_at: datetime
    method: str
    settlement_id: str | None = None


class Settlement(Frozen):
    id: str
    utr: str | None = None
    payout: Paise
    fees: Paise
    tax: Paise
    adjustments: Paise
    settled_at: date
    payment_ids: list[str]


class BankLine(Frozen):
    id: str
    value_date: date
    narration: str
    credit: Paise
    debit: Paise
    balance: Paise


class Evidence(Frozen):
    field: str
    value: str
    source_id: str


class MatchGroup(Frozen):
    id: str
    invoice_ids: list[str]
    payment_ids: list[str]
    settlement_id: str | None = None
    bank_line_id: str | None = None
    status: Literal["auto", "assisted", "open"]
    pass_id: PassId
    confidence: float
    residual: Paise
    evidence: list[Evidence]


class RecordRef(Frozen):
    kind: Literal["invoice", "payment", "settlement", "bank_line"]
    id: str


class RejectedProposal(Frozen):
    proposed_by: Literal["llm"]
    match_group: dict[str, object]
    failed_check: str


class Exception_(Frozen):
    id: str
    code: ExceptionCode
    severity: Literal[1, 2, 3]
    amount_at_risk: Paise
    records: list[RecordRef]
    attempted: list[str]
    explanation: str | None = None
    suggested_action: str | None = None
    rejected_proposal: RejectedProposal | None = None


class ClassScore(Frozen):
    precision: float | None = None
    recall: float | None = None
    count: int


class RunMetrics(Frozen):
    auto_rate: float
    assist_rate: float
    open_rate: float
    precision: float | None = None
    recall: float | None = None
    false_matches: int | None = None
    by_class: dict[str, ClassScore] | None = None
    records: int
    throughput_rps: float
    p50_ms: int
    p95_ms: int
    llm_requests: int
    llm_tokens: int
    llm_degraded: bool
    output_hash: str


class ForecastDay(Frozen):
    date: date
    recognised: Paise
    blocked: Paise


class CashForecast(Frozen):
    """A 14-day projection over the corpus's own settlement window (not the
    calendar "today" -- this is a historical synthetic corpus, not a live
    ledger), computed once by the worker so the frontend never sums a paise
    amount itself."""

    days: list[ForecastDay]
    unrecognised_cash: Paise
