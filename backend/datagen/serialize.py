import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from contracts.corpus import Corpus
from contracts.money import Paise
from datagen.difficulty import DifficultyClass
from datagen.models import Truth, TruthGroup
from money.parse import format_paise

_FILENAMES = {
    "invoices": "invoices.csv",
    "payments": "payments.csv",
    "settlements": "settlements.csv",
    "bank_lines": "bank_lines.csv",
}

# Fields rendered as canonical rupee strings (e.g. "1,234.56") rather than raw
# paise ints, and datetime fields truncated to their date -- this is what makes
# the exported CSVs re-ingestable through ingest/: a real bank/ledger export
# never contains a raw paise integer or a full timestamp for these columns,
# and money/dates already knows how to parse exactly this shape back.
_MONEY_FIELDS = {"amount", "gross", "fee", "tax", "net", "payout", "fees", "adjustments", "credit", "debit", "balance"}
_DATE_ONLY_FIELDS = {"captured_at"}


def write_corpus(corpus: Corpus, truth: Truth, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _FILENAMES["invoices"], [_row_for(i.model_dump(mode="json")) for i in corpus.invoices])
    _write_csv(output_dir / _FILENAMES["payments"], [_row_for(p.model_dump(mode="json")) for p in corpus.payments])
    _write_csv(
        output_dir / _FILENAMES["settlements"], [_row_for(s.model_dump(mode="json")) for s in corpus.settlements]
    )
    _write_csv(
        output_dir / _FILENAMES["bank_lines"], [_row_for(b.model_dump(mode="json")) for b in corpus.bank_lines]
    )
    (output_dir / "truth.json").write_text(json.dumps(truth_to_dict(truth), indent=2, sort_keys=True))


def _row_for(fields: dict[str, Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for key, value in fields.items():
        if key in _MONEY_FIELDS and value is not None:
            row[key] = format_paise(Paise(value))
        elif key in _DATE_ONLY_FIELDS and value is not None:
            row[key] = str(value).split("T", 1)[0]
        elif value is None:
            row[key] = ""
        elif isinstance(value, list):
            row[key] = ";".join(value)
        else:
            row[key] = str(value)
    return row


def truth_to_dict(truth: Truth) -> dict[str, object]:
    return {
        "groups": {gid: asdict(group) for gid, group in truth.groups.items()},
        "record_group": dict(truth.record_group),
        "record_difficulty": {key: value.value for key, value in truth.record_difficulty.items()},
    }


def truth_from_dict(data: dict[str, Any]) -> Truth:
    """The inverse of truth_to_dict -- used to restore a generated dataset's
    ground truth for scoring when a run executes against a persisted dataset
    rather than a freshly generated corpus."""
    groups = {gid: TruthGroup(**group) for gid, group in data["groups"].items()}
    record_difficulty = {key: DifficultyClass(value) for key, value in data["record_difficulty"].items()}
    return Truth(groups=groups, record_group=dict(data["record_group"]), record_difficulty=record_difficulty)


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
