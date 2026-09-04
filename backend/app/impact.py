"""What a run was worth, expressed in payments, rupees and hours.

Everything else the scoreboard reports is a quality metric: how right the
engine was. None of them answer the question a finance lead actually asks --
what did this save me -- and a rate cannot answer it on its own, because
76.4% of an unstated payment total could be three chains or three hundred.

The arithmetic here is deliberately *derived at render time* rather than
stored on the run. The per-match figure below is an assumption, not a
measurement, and baking an assumption into a persisted record means every
stored run silently changes meaning the day it is revised. A run should be a
record of what happened; this is a reading of it.

Mirrored in `frontend/lib/impact.ts`, which must agree with this file.
"""

from dataclasses import dataclass

from contracts.models import RunMetrics

# How long one chain takes a person to tie out by hand: pull the settlement
# report, find the payments behind it, match them to invoices, then find the
# credit on the bank statement. Ninety seconds is a deliberately conservative
# figure for that -- the point of showing the assumption on screen is that a
# reader who thinks it is wrong can substitute their own and the claim still
# stands in shape, so it is better to be doubted for being too low.
SECONDS_PER_MANUAL_MATCH = 90


@dataclass(frozen=True)
class RunImpact:
    cleared_without_a_human: int
    payments_total: int
    still_needs_a_human: int
    amount_cleared: int
    amount_at_risk: int
    seconds_saved: int

    @property
    def hours_saved(self) -> float:
        return self.seconds_saved / 3600


def run_impact(metrics: RunMetrics) -> RunImpact | None:
    """None when the run predates the counts this needs.

    Older runs stored only the rates, and there is no honest way to recover a
    payment count from a percentage. A missing panel is the right answer --
    the same choice the scoreboard makes for accuracy with no answer key.
    """
    if metrics.payments_total is None or metrics.payments_auto is None or metrics.payments_assisted is None:
        return None

    # Both halves are verifier-gated: an assisted match was proposed by the
    # model but recomputed in integer paise before it was written, exactly
    # like an automatic one. Counting them together is therefore not a
    # generosity -- they are the same claim, reached two ways.
    closed = metrics.payments_auto + metrics.payments_assisted

    # Open exceptions are explicitly *not* counted as saved. They are the work
    # that is left, and a tool claiming credit for the pile it is handing back
    # is the exact dishonesty the rest of this product avoids.
    return RunImpact(
        cleared_without_a_human=closed,
        payments_total=metrics.payments_total,
        still_needs_a_human=metrics.open_exceptions,
        amount_cleared=int(metrics.amount_cleared or 0),
        amount_at_risk=int(metrics.amount_at_risk),
        seconds_saved=closed * SECONDS_PER_MANUAL_MATCH,
    )


def format_duration(seconds: int) -> str:
    """Hours and minutes, in the unit the figure deserves.

    A run that saves four minutes should say four minutes. Rounding it up to
    "0.1 hours" to keep one unit throughout would be inflating a small number
    into an impressive-looking one, which is the failure mode this whole panel
    exists to avoid.
    """
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h {remainder:02d}m" if remainder else f"{hours}h"


__all__ = ["SECONDS_PER_MANUAL_MATCH", "RunImpact", "format_duration", "run_impact"]
