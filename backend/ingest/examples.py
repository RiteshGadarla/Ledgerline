"""Worked examples for the schema-mapping prompt.

The mapper asks a model to name a column, and the columns it gets wrong are
never the obvious ones -- they are the pairs where two headers are both
plausible and only one is right. A settlement export carrying both a gross and
a net is the case that matters: the payout is the net, because the net is what
the bank actually credits, and a model that picks the gross puts every later
comparison off by exactly the fee.

That is not a fact about English, so instructing harder does not fix it. It is
a convention of Indian payment reporting, and conventions are taught by
example. Each role below carries fully worked mappings of files it might
plausibly be handed, answers included, alongside the header lexicon in
`ingest/headers.py` rendered as example pairs -- fifty or more labelled
decisions in front of every question actually asked.

These are demonstrations, not rules. The rules are in `headers.py`, applied
before the model is consulted at all, and a proposal that contradicts one is
dropped rather than merged.
"""

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ingest.mapper import SourceRole


class ExampleField(NamedTuple):
    source_header: str
    canonical_field: str | None
    confidence: float


class WorkedExample(NamedTuple):
    """One file's headers and the whole correct answer for them."""

    note: str
    fields: list[ExampleField]

    @property
    def headers(self) -> list[str]:
        return [field.source_header for field in self.fields]


_LEDGER = [
    WorkedExample(
        "A tax invoice register. The invoice's amount is the total payable, not the taxable base it was computed from.",
        [
            ExampleField("Invoice No", "id", 1.0),
            ExampleField("Party", "customer", 1.0),
            ExampleField("Bill Date", "issued_at", 1.0),
            ExampleField("Taxable Value", None, 0.0),
            ExampleField("GST", None, 0.0),
            ExampleField("Total", "amount", 0.9),
        ],
    ),
    WorkedExample(
        "Abbreviated headers. The identifier is the id even when the ledger calls it something short.",
        [
            ExampleField("inv_id", "id", 1.0),
            ExampleField("client", "customer", 1.0),
            ExampleField("dt", "issued_at", 0.9),
            ExampleField("amt", "amount", 0.9),
            ExampleField("po_ref", "ref", 0.8),
            ExampleField("currency", None, 0.0),
        ],
    ),
]

_GATEWAY = [
    WorkedExample(
        "A captured-payments export. Gross is what was captured; net is what was passed on after fee and tax.",
        [
            ExampleField("Payment Id", "id", 1.0),
            ExampleField("Order Id", "order_id", 1.0),
            ExampleField("Amount", "gross", 0.9),
            ExampleField("Fee (Rs)", "fee", 1.0),
            ExampleField("Tax (Rs)", "tax", 1.0),
            ExampleField("Settled Amount", "net", 0.9),
            ExampleField("Created At", "captured_at", 0.9),
            ExampleField("Method", "method", 1.0),
            ExampleField("Status", "status", 1.0),
        ],
    ),
    WorkedExample(
        "A terser export. MDR is the gateway's fee and GST is the tax on it.",
        [
            ExampleField("txn_ref", "id", 0.9),
            ExampleField("inv", "invoice_ref", 0.9),
            ExampleField("amt_inr", "gross", 0.9),
            ExampleField("mdr", "fee", 0.9),
            ExampleField("gst", "tax", 0.9),
            ExampleField("date", "captured_at", 0.8),
            ExampleField("customer_email", None, 0.0),
        ],
    ),
]

_SETTLEMENT = [
    WorkedExample(
        "The case that matters. The payout is the NET credited, never the gross: "
        "the bank credits the net, so the gross has no canonical field and is left unmapped.",
        [
            ExampleField("Settlement Id", "id", 1.0),
            ExampleField("Gross", None, 0.0),
            ExampleField("Commission", "fees", 0.9),
            ExampleField("GST", "tax", 0.9),
            ExampleField("Net Credit", "payout", 1.0),
            ExampleField("Date", "settled_at", 0.9),
            ExampleField("Bank UTR", "utr", 1.0),
            ExampleField("Payments", "payment_ids", 0.9),
        ],
    ),
    WorkedExample(
        "A payout report. An RRN is the same kind of bank reference as a UTR.",
        [
            ExampleField("payout_id", "id", 1.0),
            ExampleField("amount_settled", "payout", 0.9),
            ExampleField("charges", "fees", 0.8),
            ExampleField("tax_amt", "tax", 0.9),
            ExampleField("credited_on", "settled_at", 0.9),
            ExampleField("rrn", "utr", 0.8),
            ExampleField("status", None, 0.0),
        ],
    ),
]

_BANK = [
    WorkedExample(
        "A bank statement with separate columns. A deposit is a credit and a withdrawal is a debit.",
        [
            ExampleField("Date", "value_date", 0.9),
            ExampleField("Narration", "narration", 1.0),
            ExampleField("Chq/Ref No", None, 0.0),
            ExampleField("Withdrawal Amt.", "debit", 1.0),
            ExampleField("Deposit Amt.", "credit", 1.0),
            ExampleField("Closing Balance", "balance", 1.0),
        ],
    ),
    WorkedExample(
        "A statement with one signed amount column. It maps to credit: an outgoing "
        "arrives as a negative one and is read as a debit downstream. Never leave it unmapped.",
        [
            ExampleField("Txn Date", "value_date", 1.0),
            ExampleField("Description", "narration", 1.0),
            ExampleField("Amount", "credit", 0.8),
            ExampleField("Dr/Cr", None, 0.0),
            ExampleField("Balance", "balance", 1.0),
        ],
    ),
]

WORKED_EXAMPLES: dict["SourceRole", list[WorkedExample]] = {
    "ledger": _LEDGER,
    "gateway": _GATEWAY,
    "settlement": _SETTLEMENT,
    "bank": _BANK,
}
