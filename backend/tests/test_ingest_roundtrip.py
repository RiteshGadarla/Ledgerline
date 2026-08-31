from pathlib import Path

from contracts.corpus import Corpus
from contracts.models import BankLine, Invoice, Payment, Settlement
from datagen.generator import generate_corpus
from datagen.serialize import write_corpus
from engine.pipeline import match
from ingest.mapper import CANONICAL_FIELDS, MappingResponse, SourceRole
from ingest.tabular import parse_table
from ingest.validate import build_records
from money.result import Ok
from scripts.eval import score_run

_ROLE_FOR_FILE: dict[str, SourceRole] = {
    "invoices.csv": "ledger",
    "payments.csv": "gateway",
    "settlements.csv": "settlement",
    "bank_lines.csv": "bank",
}


def _identity_mapping(headers: list[str], role: SourceRole) -> MappingResponse:
    """The exported CSVs already use canonical field names as headers, so the
    mapping the LLM would return here is the identity mapping -- this test
    isolates the parse/validate path from the mapper's own LLM plumbing,
    which is already covered by test_ingest_mapper.py."""
    canonical = set(CANONICAL_FIELDS[role])
    return MappingResponse.model_validate(
        {
            "fields": [
                {
                    "source_header": header,
                    "canonical_field": header if header in canonical else None,
                    "confidence": 1.0,
                }
                for header in headers
            ]
        }
    )


def _reingest_corpus(output_dir: Path) -> Corpus:
    invoices: list[Invoice] = []
    payments: list[Payment] = []
    settlements: list[Settlement] = []
    bank_lines: list[BankLine] = []

    for filename, role in _ROLE_FOR_FILE.items():
        content = (output_dir / filename).read_bytes()
        parsed = parse_table(content, filename)
        assert isinstance(parsed, Ok), parsed
        table = parsed.value
        mapping = _identity_mapping(table.headers, role)
        report = build_records(role, table, mapping)
        assert report.errors == [], report.errors
        if role == "ledger":
            invoices.extend(r for r in report.valid_records if isinstance(r, Invoice))
        elif role == "gateway":
            payments.extend(r for r in report.valid_records if isinstance(r, Payment))
        elif role == "settlement":
            settlements.extend(r for r in report.valid_records if isinstance(r, Settlement))
        else:
            bank_lines.extend(r for r in report.valid_records if isinstance(r, BankLine))

    return Corpus(invoices=invoices, payments=payments, settlements=settlements, bank_lines=bank_lines)


def test_ingested_demo_corpus_reproduces_native_run_metrics(tmp_path: Path) -> None:
    corpus, truth = generate_corpus(1001, 150)
    write_corpus(corpus, truth, tmp_path)

    reingested = _reingest_corpus(tmp_path)

    native_result = match(corpus)
    reingested_result = match(reingested)

    native_metrics = score_run(corpus, native_result, truth, elapsed_seconds=1.0)
    reingested_metrics = score_run(reingested, reingested_result, truth, elapsed_seconds=1.0)

    assert reingested_result.output_hash == native_result.output_hash
    assert reingested_metrics.auto_rate == native_metrics.auto_rate
    assert reingested_metrics.assist_rate == native_metrics.assist_rate
    assert reingested_metrics.open_rate == native_metrics.open_rate
    assert reingested_metrics.false_matches == native_metrics.false_matches == 0
