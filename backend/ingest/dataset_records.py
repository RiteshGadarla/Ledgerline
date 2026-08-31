from typing import Any

from pydantic import TypeAdapter

from contracts.corpus import Corpus
from contracts.models import BankLine, Invoice, Payment, Settlement
from ingest.mapper import SourceRole

ROLE_TO_CORPUS_FIELD: dict[SourceRole, str] = {
    "ledger": "invoices",
    "gateway": "payments",
    "settlement": "settlements",
    "bank": "bank_lines",
}

_ADAPTERS: dict[SourceRole, TypeAdapter[Any]] = {
    "ledger": TypeAdapter(list[Invoice]),
    "gateway": TypeAdapter(list[Payment]),
    "settlement": TypeAdapter(list[Settlement]),
    "bank": TypeAdapter(list[BankLine]),
}


def records_to_json(role: SourceRole, records: list[Any]) -> str:
    return _ADAPTERS[role].dump_json(records).decode()


def records_from_json(role: SourceRole, records_json: str) -> list[Any]:
    result: list[Any] = _ADAPTERS[role].validate_json(records_json)
    return result


def build_corpus(records_json_by_role: dict[SourceRole, str]) -> Corpus:
    """records_json_by_role must carry all four required roles -- callers are
    responsible for having already checked a dataset's status is "ready"."""
    return Corpus(
        invoices=records_from_json("ledger", records_json_by_role["ledger"]),
        payments=records_from_json("gateway", records_json_by_role["gateway"]),
        settlements=records_from_json("settlement", records_json_by_role["settlement"]),
        bank_lines=records_from_json("bank", records_json_by_role["bank"]),
    )
