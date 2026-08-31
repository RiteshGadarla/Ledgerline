import re
import unicodedata

from contracts.money import Paise
from money.result import Err, Ok, Result

_UNICODE_SPACES = re.compile("[\\s  -​  　]+")
_CURRENCY_PREFIX = re.compile(r"^(₹|rs\.?|inr|usd|\$)\s*", re.IGNORECASE)
_TRAILING_SIGN = re.compile(r"\s*(cr|dr)\.?$", re.IGNORECASE)
_AMOUNT_SHAPE = re.compile(r"^-?\d+(?:,\d+)*(?:\.\d+)?$")


def parse_amount(value: str | int) -> Result[Paise]:
    """Parse a rupee amount into exact paise. Never raises."""
    if isinstance(value, bool):
        return Err(f"unsupported type: {type(value).__name__}")
    if isinstance(value, int):
        return Ok(Paise(value))
    if not isinstance(value, str):
        return Err(f"unsupported type: {type(value).__name__}")

    text = unicodedata.normalize("NFKC", value)
    text = _UNICODE_SPACES.sub("", text).strip()
    if not text:
        return Err("empty amount")

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    sign_match = _TRAILING_SIGN.search(text)
    if sign_match:
        if sign_match.group(1).lower() == "dr":
            negative = True
        text = text[: sign_match.start()]

    text = _CURRENCY_PREFIX.sub("", text).strip()

    if text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]

    if not text or not _AMOUNT_SHAPE.match(text):
        return Err(f"unrecognised amount shape: {value!r}")

    integer_part, _, fraction_part = text.replace(",", "").partition(".")
    if not integer_part:
        return Err(f"unrecognised amount shape: {value!r}")

    fraction_part = (fraction_part + "00")[:2]
    try:
        rupees = int(integer_part)
        paise_fraction = int(fraction_part)
    except ValueError:
        return Err(f"unrecognised amount shape: {value!r}")

    total = rupees * 100 + paise_fraction
    if negative:
        total = -total
    return Ok(Paise(total))


def _indian_group(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    last_three = digits[-3:]
    rest = digits[:-3]
    groups: list[str] = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return ",".join([*groups, last_three])


def format_paise(value: Paise) -> str:
    """Canonical Indian-grouped rupee string for a paise amount."""
    magnitude = abs(int(value))
    rupees, paise = divmod(magnitude, 100)
    sign = "-" if value < 0 else ""
    return f"{sign}{_indian_group(str(rupees))}.{paise:02d}"
