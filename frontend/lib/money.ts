/**
 * The one sanctioned place in web/ that touches a paise integer arithmetically
 * -- everywhere else, amounts arrive from the API already summed, rated, or
 * scored, and this file only ever converts a single already-final number into
 * a display string (mirroring backend/money/parse.py::format_paise exactly,
 * integer-only, no floating point). eslint.config.mjs exempts only this file
 * from the amount-arithmetic-ban rule; nowhere else may divide, multiply,
 * add, or subtract a value whose name matches an amount/paise pattern.
 */
export function formatPaise(paise: number): string {
  const negative = paise < 0;
  const magnitude = Math.abs(Math.trunc(paise));
  const rupees = Math.trunc(magnitude / 100);
  const subunits = magnitude % 100;
  const grouped = indianGroup(String(rupees));
  const sign = negative ? "-" : "";
  return `${sign}${grouped}.${String(subunits).padStart(2, "0")}`;
}

function indianGroup(digits: string): string {
  if (digits.length <= 3) return digits;
  const lastThree = digits.slice(-3);
  let rest = digits.slice(0, -3);
  const groups: string[] = [];
  while (rest.length > 2) {
    groups.unshift(rest.slice(-2));
    rest = rest.slice(0, -2);
  }
  if (rest) groups.unshift(rest);
  return [...groups, lastThree].join(",");
}

export function formatRupees(paise: number): string {
  return `₹${formatPaise(paise)}`;
}
