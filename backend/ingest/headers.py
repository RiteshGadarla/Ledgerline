"""Deterministic column mapping: the part of schema inference that is not a
judgement call.

The model is asked to map columns because a real export names them anything.
But most of what it is asked is not ambiguous at all -- `payment_id` is the
payment's id in every gateway export ever written -- and asking a model a
question with one right answer just adds a way to get it wrong. It did:
against one real four-file upload it mapped a ledger's `invoice_id` to
`number` (leaving the invoice with a synthesised id nothing could cite), a
settlement's `gross_amount` to `payout` (the payout is the *net*), and left a
bank statement's `amount` column unmapped entirely, which zeroed every credit.
Three wrong answers, none of them close calls, and the run tied out nothing.

So the known names are resolved here, first, by a table. The model is asked
only about the headers this table has never seen, and an answer of its that
collides with a resolved field is dropped rather than merged. Same discipline
as the verifier: propose freely, but a deterministic check decides.

Each canonical field lists its accepted header names in priority order, which
is what settles a file carrying two plausible candidates: a settlement with
both `gross_amount` and `net_payout` resolves `payout` to the net one because
that is the figure the bank actually credits.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ingest.mapper import SourceRole

# canonical field -> accepted header names, best first.
_FIELD_HEADERS: dict["SourceRole", dict[str, list[str]]] = {
    "ledger": {
        "id": [
            "invoice id",
            "invoice no",
            "invoice number",
            "inv id",
            "inv no",
            "bill no",
            "bill number",
            "document no",
            "document number",
        ],
        "ref": ["invoice ref", "reference", "reference no", "ref", "ref no"],
        "customer": [
            "customer name",
            "customer",
            "party name",
            "party",
            "client",
            "client name",
            "buyer",
            "billed to",
            "vendor",
        ],
        "amount": [
            "amount due",
            "invoice amount",
            "invoice value",
            "amount",
            "total amount",
            "total",
            "value",
            "gross amount",
        ],
        "issued_at": ["invoice date", "issue date", "issued at", "issued on", "bill date", "date"],
        "number": ["invoice number", "number"],
    },
    "gateway": {
        "id": ["payment id", "razorpay payment id", "gateway payment id", "transaction id", "txn id"],
        "order_id": ["order id", "receipt", "receipt no"],
        "invoice_ref": ["invoice ref", "invoice id", "invoice no", "invoice number", "invoice"],
        "gross": ["amount captured", "captured amount", "gross amount", "gross", "payment amount", "amount"],
        "fee": ["gateway fee", "fee", "fees", "mdr", "commission"],
        "tax": ["gst on fee", "tax on fee", "gst", "tax", "service tax"],
        "net": ["net amount", "amount settled", "net"],
        "status": ["payment status", "status", "state"],
        "captured_at": ["payment date", "captured at", "capture date", "created at", "transaction date", "date"],
        "method": ["payment method", "payment mode", "method", "mode", "instrument"],
        "settlement_id": ["settlement id", "payout id", "batch id"],
    },
    "settlement": {
        "id": ["settlement id", "payout id", "batch id"],
        "utr": ["utr number", "utr no", "utr", "bank reference", "bank ref", "rrn"],
        # The payout is the net figure: it is what lands in the bank, and it is
        # what a bank credit is compared against.
        "payout": ["net payout", "net amount", "amount credited", "settlement amount", "payout amount", "payout"],
        "fees": ["gateway fee", "settlement fee", "fees", "fee", "commission", "mdr"],
        "tax": ["gst on fee", "tax on fee", "gst", "tax"],
        "adjustments": ["adjustments", "adjustment", "adjustment amount"],
        "settled_at": ["settlement date", "settled at", "payout date", "credit date", "value date", "date"],
        "payment_ids": ["payment ids", "payment refs", "payment ref", "payment id", "payments", "payment references"],
    },
    "bank": {
        "id": ["txn id", "transaction id", "reference id", "sl no", "serial no", "s no"],
        "value_date": ["value date", "transaction date", "txn date", "posting date", "book date", "date"],
        # Priority matters: a statement carrying both a narration and a bare
        # reference column keeps the narration, which is the richer of the two
        # and the one the UTR is usually written into.
        "narration": [
            "narration",
            "description",
            "particulars",
            "remarks",
            "details",
            "transaction remarks",
            "reference no",
            "ref no",
        ],
        "credit": ["credit amount", "credit", "deposit", "deposits", "amount credited", "cr", "amount"],
        "debit": ["debit amount", "debit", "withdrawal", "withdrawals", "amount debited", "dr"],
        "balance": ["closing balance", "running balance", "balance", "available balance"],
    },
}

# Headers that carry no reconcilable content. Named so the model is not asked
# about them and cannot invent a home for them.
_IGNORED: dict["SourceRole", list[str]] = {
    "ledger": ["currency", "status", "due date", "notes", "gst", "tax", "sr no", "s no", "serial no"],
    "gateway": [
        "currency",
        "customer name",
        "customer",
        "email",
        "contact",
        "phone",
        "notes",
        "description",
        "card network",
        "bank",
    ],
    "settlement": ["currency", "status", "notes", "type"],
    "bank": ["type", "cr dr", "dr cr", "transaction type", "cheque no", "branch", "currency"],
}


def normalise(header: str) -> str:
    """Lower case, punctuation to spaces, whitespace collapsed."""
    cleaned = "".join(character if character.isalnum() else " " for character in header.lower())
    return " ".join(cleaned.split())


def deterministic_mapping(role: "SourceRole", headers: list[str]) -> dict[str, str | None]:
    """Resolve what the table knows. Returns only the headers it is sure of:
    a header absent from the result is one the model still has to answer for.

    A canonical field is claimed once. Two headers that both look like the
    payout do not both become the payout; the higher-priority name wins and
    the loser is left unresolved rather than silently mapped somewhere wrong.
    """
    by_normalised: dict[str, list[str]] = {}
    for header in headers:
        by_normalised.setdefault(normalise(header), []).append(header)

    resolved: dict[str, str | None] = {}
    claimed: set[str] = set()

    for field, candidates in _FIELD_HEADERS[role].items():
        for candidate in candidates:
            if field in claimed:
                break
            for header in by_normalised.get(candidate, []):
                if header in resolved:
                    continue
                resolved[header] = field
                claimed.add(field)
                break

    for ignored in _IGNORED[role]:
        for header in by_normalised.get(ignored, []):
            resolved.setdefault(header, None)

    return resolved


def claimed_fields(mapping: dict[str, str | None]) -> set[str]:
    """The canonical fields a mapping has already spoken for."""
    return {field for field in mapping.values() if field is not None}
