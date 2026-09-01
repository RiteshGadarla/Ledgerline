/**
 * The one sanctioned place in the frontend that turns an API timestamp into a
 * display string. Every `*_at` field the backend returns is a UTC instant
 * (ISO-8601 with a `Z` offset, written with `datetime.now(UTC)` and stored in
 * a Postgres TIMESTAMPTZ), so the instant itself is already correct no matter
 * what timezone the server runs in.
 *
 * What is *not* automatic is which zone it gets rendered in. A bare
 * `toLocaleString()` renders in whatever zone the viewer's browser is set to,
 * so the same run would read 11:04 PM to the Mumbai ops team and 10:34 AM to
 * someone reviewing from California -- with nothing on screen to say which.
 * These are books-and-settlement timestamps, so they are pinned to IST for
 * everyone, the same way money.ts pins lakh/crore grouping rather than
 * deferring to the browser's locale.
 *
 * Date-only fields (value_date, captured_at, issued_at, settled_at, forecast
 * day.date) are business dates, not instants -- they must be rendered as the
 * raw `YYYY-MM-DD` string the API sends and never passed through here, since
 * parsing one as a Date makes it UTC midnight and shifts it a day backwards in
 * any negative-offset zone.
 */
const IST_TIMESTAMP = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: true,
});

/** Render a UTC instant from the API as `01 Sep 2026, 11:04 pm IST`. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "-";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "-";
  return `${IST_TIMESTAMP.format(parsed)} IST`;
}
