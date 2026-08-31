from datetime import date, timedelta

from contracts.corpus import Corpus
from contracts.enums import ExceptionCode
from contracts.models import CashForecast, ForecastDay
from contracts.money import Paise
from engine.pipeline import MatchResult

WINDOW_DAYS = 14


def build_forecast(corpus: Corpus, result: MatchResult) -> CashForecast:
    """A day-by-day projection over the corpus's own settlement window:
    "recognised" is payout already tied to a matched (auto/assisted) group,
    "blocked" is payout for a settlement still stuck behind an exception.
    "Unrecognised cash" is bank credit that never tied to any settlement at
    all (UNIDENTIFIED_CREDIT), reported as one separate total rather than
    folded into either day bucket, since it isn't expected income at all.

    Computed once here, on already-available run outputs, so the Cash
    position surface never sums a paise amount client-side.
    """
    if not corpus.settlements:
        return CashForecast(days=[], unrecognised_cash=Paise(0))

    window_start = min(s.settled_at for s in corpus.settlements)
    window_end = window_start + timedelta(days=WINDOW_DAYS - 1)

    recognised_settlement_ids = {g.settlement_id for g in result.groups if g.settlement_id is not None}
    blocked_settlement_ids = {
        r.id for e in result.exceptions for r in e.records if r.kind == "settlement"
    } - recognised_settlement_ids

    by_day: dict[date, dict[str, int]] = {
        window_start + timedelta(days=offset): {"recognised": 0, "blocked": 0} for offset in range(WINDOW_DAYS)
    }
    for settlement in corpus.settlements:
        if not (window_start <= settlement.settled_at <= window_end):
            continue
        bucket = by_day[settlement.settled_at]
        if settlement.id in recognised_settlement_ids:
            bucket["recognised"] += int(settlement.payout)
        elif settlement.id in blocked_settlement_ids:
            bucket["blocked"] += int(settlement.payout)

    days = [
        ForecastDay(date=day, recognised=Paise(totals["recognised"]), blocked=Paise(totals["blocked"]))
        for day, totals in sorted(by_day.items())
    ]

    unidentified_bank_line_ids = {
        r.id
        for e in result.exceptions
        if e.code == ExceptionCode.UNIDENTIFIED_CREDIT
        for r in e.records
        if r.kind == "bank_line"
    }
    unrecognised_cash = sum(
        (bank_line.credit for bank_line in corpus.bank_lines if bank_line.id in unidentified_bank_line_ids),
        start=0,
    )

    return CashForecast(days=days, unrecognised_cash=Paise(unrecognised_cash))
