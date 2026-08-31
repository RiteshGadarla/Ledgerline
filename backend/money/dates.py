import re
from datetime import date, timedelta

from money.result import Err, Ok, Result

_EXCEL_EPOCH = date(1899, 12, 30)

_DMY_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_DMY_MON_YY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})$")
_YMD_DASH = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")

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


def _build_date(year: int, month: int, day: int, original: str) -> Result[date]:
    try:
        return Ok(date(year, month, day))
    except ValueError:
        return Err(f"invalid calendar date: {original!r}")
