from datetime import timedelta

from contracts.corpus import Corpus
from contracts.enums import ExceptionCode
from datagen.generator import generate_corpus
from engine.pipeline import match
from workers.forecast import WINDOW_DAYS, build_forecast


def test_forecast_covers_a_14_day_window_starting_at_the_earliest_settlement() -> None:
    corpus, _truth = generate_corpus(1001, 150)
    result = match(corpus)

    forecast = build_forecast(corpus, result)

    window_start = min(s.settled_at for s in corpus.settlements)
    assert len(forecast.days) == WINDOW_DAYS
    assert forecast.days[0].date == window_start
    assert forecast.days[-1].date == window_start + timedelta(days=WINDOW_DAYS - 1)


def test_recognised_totals_match_settlements_in_auto_or_assisted_groups() -> None:
    corpus, _truth = generate_corpus(1001, 150)
    result = match(corpus)

    forecast = build_forecast(corpus, result)

    window_start = min(s.settled_at for s in corpus.settlements)
    window_end = window_start + timedelta(days=WINDOW_DAYS - 1)
    recognised_settlement_ids = {g.settlement_id for g in result.groups if g.settlement_id is not None}
    expected = sum(
        int(s.payout)
        for s in corpus.settlements
        if s.id in recognised_settlement_ids and window_start <= s.settled_at <= window_end
    )
    assert sum(int(day.recognised) for day in forecast.days) == expected


def test_blocked_totals_match_settlements_still_exceptioned() -> None:
    corpus, _truth = generate_corpus(1001, 150)
    result = match(corpus)

    forecast = build_forecast(corpus, result)

    recognised_settlement_ids = {g.settlement_id for g in result.groups if g.settlement_id is not None}
    blocked_settlement_ids = {
        r.id for e in result.exceptions for r in e.records if r.kind == "settlement"
    } - recognised_settlement_ids
    window_start = min(s.settled_at for s in corpus.settlements)
    window_end = window_start + timedelta(days=WINDOW_DAYS - 1)
    expected = sum(
        int(s.payout)
        for s in corpus.settlements
        if s.id in blocked_settlement_ids and window_start <= s.settled_at <= window_end
    )
    assert sum(int(day.blocked) for day in forecast.days) == expected


def test_unrecognised_cash_matches_unidentified_credit_exceptions() -> None:
    corpus, _truth = generate_corpus(1001, 150)
    result = match(corpus)

    forecast = build_forecast(corpus, result)

    unidentified_bank_line_ids = {
        r.id
        for e in result.exceptions
        if e.code == ExceptionCode.UNIDENTIFIED_CREDIT
        for r in e.records
        if r.kind == "bank_line"
    }
    expected = sum(b.credit for b in corpus.bank_lines if b.id in unidentified_bank_line_ids)
    assert int(forecast.unrecognised_cash) == expected


def test_empty_corpus_produces_an_empty_forecast() -> None:
    empty = Corpus(invoices=[], payments=[], settlements=[], bank_lines=[])
    result = match(empty)

    forecast = build_forecast(empty, result)

    assert forecast.days == []
    assert int(forecast.unrecognised_cash) == 0
