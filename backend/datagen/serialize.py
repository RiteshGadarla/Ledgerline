import csv
import json
from dataclasses import asdict
from pathlib import Path

from contracts.corpus import Corpus
from datagen.models import Truth

_FILENAMES = {
    "invoices": "invoices.csv",
    "payments": "payments.csv",
    "settlements": "settlements.csv",
    "bank_lines": "bank_lines.csv",
}


def write_corpus(corpus: Corpus, truth: Truth, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _FILENAMES["invoices"], [i.model_dump(mode="json") for i in corpus.invoices])
    _write_csv(output_dir / _FILENAMES["payments"], [p.model_dump(mode="json") for p in corpus.payments])
    _write_csv(output_dir / _FILENAMES["settlements"], [s.model_dump(mode="json") for s in corpus.settlements])
    _write_csv(output_dir / _FILENAMES["bank_lines"], [b.model_dump(mode="json") for b in corpus.bank_lines])
    (output_dir / "truth.json").write_text(json.dumps(truth_to_dict(truth), indent=2, sort_keys=True))


def truth_to_dict(truth: Truth) -> dict[str, object]:
    return {
        "groups": {gid: asdict(group) for gid, group in truth.groups.items()},
        "record_group": dict(truth.record_group),
        "record_difficulty": {key: value.value for key, value in truth.record_difficulty.items()},
    }


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
