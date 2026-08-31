import re
from collections.abc import Iterable
from dataclasses import dataclass

_UTR_PATTERN = re.compile(r"\bUTR\s*(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9]{9,23})\b", re.IGNORECASE)
_RRN_PATTERN = re.compile(r"\bRRN\s*(?:NO\.?|#)?\s*[:\-]?\s*([A-Z0-9]{9,23})\b", re.IGNORECASE)
_PAY_ID_PATTERN = re.compile(r"\b(pay_[A-Za-z0-9]{6,})\b")
_ORDER_ID_PATTERN = re.compile(r"\b(order_[A-Za-z0-9]{6,})\b")
_INVOICE_PATTERN = re.compile(r"\b(INV[-_ ]?[0-9]{2,}[A-Za-z0-9_\-]*)\b", re.IGNORECASE)
_RAIL_PATTERN = re.compile(r"\b(NEFT|IMPS|UPI|RTGS)\b", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"(?<!\d)[A-Za-z][A-Za-z.&]*(?!\d)")

_NOISE_WORDS = {
    "UTR", "RRN", "TO", "FROM", "BY", "VIA", "REF", "NO", "NUMBER", "PAYMENT",
    "TRANSFER", "CREDIT", "DEBIT", "SETTLEMENT", "RAZORPAY", "NEFT", "IMPS",
    "UPI", "RTGS", "TXN", "ID", "FOR", "AND", "THE", "INV", "PAY", "ORDER",
}


@dataclass(frozen=True)
class NarrationTokens:
    utrs: list[str]
    rrns: list[str]
    pay_ids: list[str]
    order_ids: list[str]
    invoice_tokens: list[str]
    payer: str | None
    rail: str | None


def extract(narration: str) -> NarrationTokens:
    """Pull structured tokens out of a free-text bank/gateway narration. Pure regex, no model."""
    text = " ".join(narration.split())
    return NarrationTokens(
        utrs=_dedupe(m.upper() for m in _UTR_PATTERN.findall(text)),
        rrns=_dedupe(m.upper() for m in _RRN_PATTERN.findall(text)),
        pay_ids=_dedupe(_PAY_ID_PATTERN.findall(text)),
        order_ids=_dedupe(_ORDER_ID_PATTERN.findall(text)),
        invoice_tokens=_dedupe(m.upper() for m in _INVOICE_PATTERN.findall(text)),
        payer=_extract_payer(text),
        rail=_extract_rail(text),
    )


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen.keys())


def _extract_rail(text: str) -> str | None:
    match = _RAIL_PATTERN.search(text)
    return match.group(1).upper() if match else None


def _extract_payer(text: str) -> str | None:
    words = _WORD_PATTERN.findall(text)
    candidates = [w for w in words if w.upper() not in _NOISE_WORDS]
    if not candidates:
        return None
    return " ".join(candidates[:3])
