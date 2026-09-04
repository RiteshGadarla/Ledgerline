/**
 * What a run was worth, expressed in payments, rupees and hours.
 *
 * Everything else the scoreboard reports is a quality metric: how right the
 * engine was. None of them answer the question a finance lead actually asks --
 * what did this save me -- and a rate cannot answer it alone, because 76.4% of
 * an unstated payment total could be three chains or three hundred.
 *
 * Derived at render time rather than stored on the run. The per-match figure
 * below is an assumption, not a measurement, and baking an assumption into a
 * persisted record means every stored run silently changes meaning the day it
 * is revised. A run is a record of what happened; this is a reading of it.
 *
 * Mirrored from `backend/app/impact.py`, which must agree with this file.
 */

/**
 * How long one chain takes a person to tie out by hand: pull the settlement
 * report, find the payments behind it, match them to invoices, then find the
 * credit on the bank statement. Ninety seconds is deliberately conservative --
 * the assumption is shown on screen so a reader who disagrees can substitute
 * their own and the claim still stands in shape, so it is better to be doubted
 * for being too low.
 */
export const SECONDS_PER_MANUAL_MATCH = 90;

export type RunImpact = {
  clearedWithoutAHuman: number;
  paymentsTotal: number;
  stillNeedsAHuman: number;
  amountCleared: number;
  amountAtRisk: number;
  secondsSaved: number;
};

type ImpactInput = {
  payments_total?: number | null;
  payments_auto?: number | null;
  payments_assisted?: number | null;
  amount_cleared?: number | null;
  amount_at_risk: number;
  open_exceptions: number;
};

/**
 * `null` when the run predates the counts this needs. Older runs stored only
 * the rates, and there is no honest way to recover a payment count from a
 * percentage -- so the panel is absent, the same choice the scoreboard makes
 * for accuracy with no answer key.
 */
export function runImpact(m: ImpactInput): RunImpact | null {
  const { payments_total: total, payments_auto: auto, payments_assisted: assisted } = m;
  if (
    total === null ||
    total === undefined ||
    auto === null ||
    auto === undefined ||
    assisted === null ||
    assisted === undefined
  ) {
    return null;
  }

  // Both halves are verifier-gated: an assisted match was proposed by the model
  // but recomputed in integer paise before it was written, exactly like an
  // automatic one. They are the same claim, reached two ways.
  const closed = auto + assisted;

  // Open exceptions are explicitly not counted as saved. They are the work that
  // is left, and a tool claiming credit for the pile it is handing back is the
  // exact dishonesty the rest of this product avoids.
  return {
    clearedWithoutAHuman: closed,
    paymentsTotal: total,
    stillNeedsAHuman: m.open_exceptions,
    amountCleared: m.amount_cleared ?? 0,
    amountAtRisk: m.amount_at_risk,
    secondsSaved: closed * SECONDS_PER_MANUAL_MATCH,
  };
}

/**
 * Hours and minutes, in the unit the figure deserves. A run that saves four
 * minutes should say four minutes; rounding it up to "0.1 hours" to keep one
 * unit throughout would inflate a small number into an impressive-looking one,
 * which is the failure mode this whole panel exists to avoid.
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours}h ${String(remainder).padStart(2, "0")}m` : `${hours}h`;
}
