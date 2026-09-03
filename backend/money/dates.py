import re
from datetime import date, datetime, timedelta, timezone

from money.result import Err, Ok, Result

_EXCEL_EPOCH = date(1899, 12, 30)

# The books are kept in IST, so an instant that carries its own offset is
# converted before the calendar date is read off it: a payment captured at
# 2026-08-04T21:00:00Z belongs to the 5th here, not the 4th, and a settlement
# window that got that wrong would be a day out.
_IST = timezone(timedelta(hours=5, minutes=30))

_DMY_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DMY_MON_YY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})$")
_YMD_DASH = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")

# A trailing wall-clock time, with or without seconds, meridiem or zone. Real
# exports date a row to the second -- Razorpay's own payments CSV writes
# `created_at` as `2026-08-04 10:23:45` -- and a file is not unreadable
# because it was precise about when something happened.
_TIME_SUFFIX = re.compile(
    r"""[ T]
        \d{1,2}:\d{2}          # hh:mm
        (?::\d{2}(?:\.\d+)?)?  # optional :ss.fff
        \s*(?:am|pm)?          # optional meridiem
        \s*(?:Z|[+-]\d{2}:?\d{2})?$  # optional zone
    """,
    re.IGNORECASE | re.VERBOSE,
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_date(value: str | int | float) -> Result[date]:
    """Normalise a date string or Excel serial to an IST calendar date. Never raises."""
    if isinstance(value, bool):
        return Err(f"unsupported type: {type(value).__name__}")
    if isinstance(value, int | float):
        try:
            return Ok(_EXCEL_EPOCH + timedelta(days=int(value)))
        except OverflowError:
            return Err(f"excel serial out of range: {value!r}")

    if not isinstance(value, str):
        return Err(f"unsupported type: {type(value).__name__}")

    text = value.strip()
    if not text:
        return Err("empty date")

    # An ISO instant is read as an instant: only this branch knows the offset,
    # so it is the only one that can put the row on the right side of
    # midnight. Anything fromisoformat rejects falls through to the shapes.
    if iso := _parse_iso(text):
        return Ok(iso)

    # Everything below is a calendar date, so a time of day rides along as
    # noise: drop it and match the shape that remains. A naive timestamp is
    # already a local reading, and local here is IST.
    text = _TIME_SUFFIX.sub("", text).strip()

    if match := _DMY_SLASH.match(text):
        day, month, year = (int(g) for g in match.groups())
        return _build_date(year, month, day, value)

    if match := _DMY_MON_YY.match(text):
        day_str, mon_str, yy_str = match.groups()
        month_num = _MONTHS.get(mon_str.lower())
        if month_num is None:
            return Err(f"unrecognised month: {value!r}")
        return _build_date(2000 + int(yy_str), month_num, int(day_str), value)

    if match := _YMD_DASH.match(text):
        year, month, day = (int(g) for g in match.groups())
        return _build_date(year, month, day, value)

    return Err(f"unrecognised date shape: {value!r}")


def _parse_iso(text: str) -> date | None:
    """`2026-08-04T10:23:45+05:30` and friends. Returns None -- not an error --
    for anything that isn't ISO, so the caller can try the other shapes."""
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return moment.date()
    return moment.astimezone(_IST).date()


def _build_date(year: int, month: int, day: int, original: str) -> Result[date]:
    try:
        return Ok(date(year, month, day))
    except ValueError:
        return Err(f"invalid calendar date: {original!r}")
