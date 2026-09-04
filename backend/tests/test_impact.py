from app.impact import SECONDS_PER_MANUAL_MATCH, format_duration, run_impact
from contracts.models import RunMetrics
from contracts.money import Paise


def _metrics(**overrides: object) -> RunMetrics:
    base: dict[str, object] = {
        "auto_rate": 0.75,
        "assist_rate": 0.05,
        "open_rate": 0.20,
        "records": 400,
        "open_exceptions": 24,
        "amount_at_risk": Paise(1_250_000),
        "throughput_rps": 100.0,
        "p50_ms": 6000,
        "p95_ms": 6000,
        "llm_requests": 4,
        "llm_tokens": 697,
        "llm_degraded": False,
        "output_hash": "deadbeef",
        "payments_total": 100,
        "payments_auto": 75,
        "payments_assisted": 5,
        "amount_cleared": Paise(48_000_000),
    }
    base.update(overrides)
    return RunMetrics(**base)  # type: ignore[arg-type]


def test_impact_counts_both_verified_routes_as_closed_without_a_human() -> None:
    """An assisted match was recomputed in integer paise before it was
    written, exactly like an automatic one -- they are the same claim reached
    two ways, so counting them together is not a generosity."""
    impact = run_impact(_metrics())
    assert impact is not None
    assert impact.cleared_without_a_human == 80
    assert impact.payments_total == 100
    assert impact.seconds_saved == 80 * SECONDS_PER_MANUAL_MATCH


def test_open_exceptions_are_never_counted_as_time_saved() -> None:
    """The pile being handed back is the work that is left. A tool claiming
    credit for it would be the exact dishonesty this product avoids."""
    impact = run_impact(_metrics(payments_auto=0, payments_assisted=0))
    assert impact is not None
    assert impact.seconds_saved == 0
    assert impact.cleared_without_a_human == 0
    assert impact.still_needs_a_human == 24, "the exceptions are still reported, just not as a saving"


def test_a_run_from_before_these_counts_existed_shows_no_impact_panel() -> None:
    """There is no honest way to recover a payment count from a percentage,
    so the answer is an absent panel, not an estimated one."""
    assert run_impact(_metrics(payments_total=None, payments_auto=None, payments_assisted=None)) is None


def test_money_cleared_and_money_held_are_both_reported() -> None:
    impact = run_impact(_metrics())
    assert impact is not None
    assert impact.amount_cleared == 48_000_000
    assert impact.amount_at_risk == 1_250_000


def test_a_run_with_no_settlements_reports_nothing_cleared_rather_than_guessing() -> None:
    impact = run_impact(_metrics(amount_cleared=None))
    assert impact is not None
    assert impact.amount_cleared == 0


def test_a_small_saving_is_reported_in_the_unit_it_deserves() -> None:
    """Rounding four minutes up to "0.1 hours" would inflate a small number
    into an impressive-looking one."""
    assert format_duration(45) == "45s"
    assert format_duration(240) == "4 min"
    assert format_duration(3600) == "1h"
    assert format_duration(5430) == "1h 30m"


def test_hours_saved_is_the_seconds_figure_and_not_a_second_opinion() -> None:
    impact = run_impact(_metrics())
    assert impact is not None
    assert abs(impact.hours_saved - impact.seconds_saved / 3600) < 1e-9
