"""The run report: money formatting, character safety, and a PDF that opens."""

from datetime import UTC, datetime

import pytest

from app.report import build_run_report, rupees
from db.tenancy import RunRecord

_EMPTY_RESULT = '{"groups": [], "exceptions": [], "output_hash": "9ac1f0aa3b2e"}'
_METRICS = (
    '{"auto_rate": 0.94, "assist_rate": 0.041, "open_rate": 0.06, "precision": 1.0, "recall": 0.92,'
    ' "false_matches": 0, "records": 1400, "open_exceptions": 17, "amount_at_risk": 24831000,'
    ' "throughput_rps": 1238.4, "p50_ms": 1, "p95_ms": 3, "llm_requests": 2, "llm_tokens": 411,'
    ' "llm_degraded": false, "output_hash": "9ac1f0aa3b2e"}'
)


def _run(**overrides: object) -> RunRecord:
    base = {
        "id": "7f3c9a21-0000-4000-8000-000000000000",
        "user_id": "u1",
        "source": "demo",
        "seed": 1001,
        "dataset_id": None,
        "size": 400,
        "mutations": None,
        "state": "complete",
        "error": None,
        "result_json": _EMPTY_RESULT,
        "metrics_json": _METRICS,
        "forecast_json": None,
        "created_at": datetime(2026, 9, 4, 2, 24, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 4, 2, 24, 6, tzinfo=UTC),
    }
    return RunRecord(**{**base, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("paise", "expected"),
    [
        (0, "INR 0.00"),
        (5, "INR 0.05"),
        (100, "INR 1.00"),
        (99999, "INR 999.99"),
        (100000, "INR 1,000.00"),
        # Indian grouping: lakhs and crores break every two digits, not three.
        (24831000, "INR 2,48,310.00"),
        (4120000000, "INR 4,12,00,000.00"),
        (-150000, "-INR 1,500.00"),
    ],
)
def test_rupees_groups_the_indian_way(paise: int, expected: str) -> None:
    assert rupees(paise) == expected


def test_rupees_never_loses_the_last_two_digits() -> None:
    """Paise are split with integer arithmetic, never divided into a float
    that would round the fraction away."""
    assert rupees(1234567891) == "INR 1,23,45,678.91"


def test_report_is_a_pdf_that_opens() -> None:
    pdf = build_run_report(_run())

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 2000


def test_report_states_the_corruptions_a_sabotaged_run_was_put_through() -> None:
    """A figure measured on deliberately corrupted books must never be read as
    though it came from clean ones."""
    plain = build_run_report(_run())
    sabotaged = build_run_report(_run(mutations=["scramble_narration", "shift_date:45"]))

    assert len(sabotaged) > len(plain)


def test_a_run_with_no_result_is_refused_rather_than_rendered_empty() -> None:
    with pytest.raises(AssertionError):
        build_run_report(_run(result_json=None))


def test_exceptions_and_forecast_render() -> None:
    result = (
        '{"groups": [{"id": "g1", "invoice_ids": ["INV1"], "payment_ids": ["PAY1"],'
        ' "settlement_id": "STL1", "bank_line_id": "BNK1", "status": "auto", "pass_id": "P1",'
        ' "confidence": 1.0, "residual": 0, "evidence": []}],'
        ' "exceptions": [{"id": "e1", "code": "AMT_MISMATCH_UNEXPLAINED", "severity": 3,'
        ' "amount_at_risk": 4400000, "records": [{"kind": "settlement", "id": "STL1"}],'
        ' "attempted": ["P1"], "suggested_action": "Recompute the payout from its own batch"}],'
        ' "output_hash": "9ac1f0aa3b2e"}'
    )
    forecast = '{"days": [{"date": "2026-08-04", "recognised": 500000, "blocked": 120000}], "unrecognised_cash": 90000}'

    pdf = build_run_report(_run(result_json=result, forecast_json=forecast))

    assert pdf.startswith(b"%PDF-")


def test_text_outside_latin_1_does_not_break_the_render() -> None:
    """Core PDF fonts speak latin-1. A suggested action carrying a rupee sign
    or an em dash must transliterate, not raise."""
    result = (
        '{"groups": [], "exceptions": [{"id": "e1", "code": "UNIDENTIFIED_CREDIT", "severity": 2,'
        ' "amount_at_risk": 100, "records": [{"kind": "bank_line", "id": "BNK1"}], "attempted": ["P1"],'
        ' "suggested_action": "Trace \\u20b9500 \\u2014 the credit\\u2019s narration"}],'
        ' "output_hash": "9ac1f0aa3b2e"}'
    )

    pdf = build_run_report(_run(result_json=result))

    assert pdf.startswith(b"%PDF-")


def test_accuracy_is_absent_rather_than_dashed_when_there_is_no_answer_key() -> None:
    """An uploaded corpus has no truth file. Three dashes under a heading
    called "measured accuracy" claim a measurement that was never made, so the
    whole column is left out and the remaining two share the width."""
    unscored_metrics = (
        '{"auto_rate": 0.94, "assist_rate": 0.0, "open_rate": 0.06, "precision": null,'
        ' "recall": null, "false_matches": null, "records": 224, "open_exceptions": 26,'
        ' "amount_at_risk": 24831000, "throughput_rps": 38.1, "p50_ms": 1, "p95_ms": 3,'
        ' "llm_requests": 1, "llm_tokens": 213, "llm_degraded": false, "output_hash": "9ac1f0aa3b2e"}'
    )

    scored = build_run_report(_run())
    unscored = build_run_report(_run(source="dataset", seed=None, metrics_json=unscored_metrics))

    assert unscored.startswith(b"%PDF-")
    # The scored report carries a third column and the sentence explaining it.
    assert len(scored) > len(unscored)
