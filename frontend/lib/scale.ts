/**
 * Presentation geometry: the second sanctioned place in web/ that divides an
 * amount, and the narrower one. `lib/money.ts` turns a final paise integer
 * into a display string; this file turns one into a CSS length so a bar can
 * be drawn at the right height.
 *
 * The output is never a reported figure: it is a percentage of a chart's own
 * axis, it is never shown to the user as a number, and it never feeds back
 * into anything the API is asked about. Every input is already final: summed,
 * rated or scored by the engine and re-checked by the verifier before it left
 * the backend. Nothing here derives a new financial fact.
 *
 * eslint.config.mjs exempts this file from the amount-arithmetic ban alongside
 * lib/money.ts. Nowhere else may divide, multiply, add or subtract a value
 * whose name matches an amount/paise pattern.
 */

/** A percentage of `ceiling`, clamped to [0, 100] and safe when ceiling is 0. */
export function percentOf(value: number, ceiling: number): number {
  if (ceiling <= 0) return 0;
  const pct = (value / ceiling) * 100;
  return Math.max(0, Math.min(100, pct));
}

/** `percentOf` as a CSS width/height string. */
export function percentCss(value: number, ceiling: number): string {
  return `${percentOf(value, ceiling).toFixed(2)}%`;
}

/**
 * How many of a 40-step tick scale to light for a 0..1 rate. A non-zero rate
 * always lights at least one step, so "small but present" never reads as zero.
 */
export function ticksForRate(rate: number): number {
  if (!Number.isFinite(rate) || rate <= 0) return 0;
  return Math.max(1, Math.min(40, Math.round(rate * 40)));
}

/**
 * A stacked chart's axis: the tallest stack across the window, plus headroom.
 * Each stack arrives as its already-final segment amounts; summing them here
 * produces an axis bound, never a figure shown to anyone.
 */
export function ceilingForStacks(stacks: number[][], headroom = 1.12): number {
  const tallest = stacks.reduce((max, parts) => {
    const total = parts.reduce((sum, part) => sum + part, 0);
    return total > max ? total : max;
  }, 0);
  return tallest > 0 ? tallest * headroom : 1;
}
