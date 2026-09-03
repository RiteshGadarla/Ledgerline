"""Measured accuracy over a held-out sweep, clean and under sabotage.

`eval.py` pins three golden seeds so a regression in the engine fails CI.
This is the other question: not "did anything change" but "how well does it
actually do, across corpora nobody tuned it on, and what happens when the
books are corrupted on purpose".

Every seed here is outside the golden set, so no number below was measured on
a corpus the engine was developed against. Run it with:

    uv run python -m scripts.benchmark            # the table
    uv run python -m scripts.benchmark --json     # the same, machine-readable
"""

import argparse
import json
import statistics
import time
from dataclasses import dataclass
from typing import TypedDict

from contracts.models import RunMetrics
from datagen.generator import generate_corpus
from datagen.models import Truth
from datagen.mutations import apply_mutations, parse_mutation
from engine.pipeline import match
from money.result import Err
from scripts.eval import GOLDEN_SEEDS, score_run

# Held out on purpose: disjoint from GOLDEN_SEEDS, and never looked at while
# the passes were being written.
HELD_OUT_SEEDS = list(range(5001, 5031))
SIZES = [150, 600, 2400]

# One representative of each corruption the mutation engine can apply, with
# the arguments the console offers.
CORRUPTIONS = [
    "duplicate_payment",
    "shift_date:45",
    "alter_amount:-150000",
    "delete_bank_line",
    "inject_unrelated_credit",
    "scramble_narration",
    "split_payment",
]


class RowSummary(TypedDict):
    """One row of the published table, in the shape `--json` emits."""

    label: str
    runs: int
    records: int
    precision: float | None
    recall: float | None
    false_matches_total: int
    false_matches_worst: int
    auto_rate: float
    open_exceptions_mean: float
    throughput_rps_median: float


@dataclass
class Row:
    label: str
    runs: int
    records: int
    precision: list[float]
    recall: list[float]
    false_matches: list[int]
    auto_rate: list[float]
    open_exceptions: list[int]
    throughput: list[float]

    def add(self, metrics: RunMetrics) -> None:
        self.runs += 1
        self.records += metrics.records
        if metrics.precision is not None:
            self.precision.append(metrics.precision)
        if metrics.recall is not None:
            self.recall.append(metrics.recall)
        if metrics.false_matches is not None:
            self.false_matches.append(metrics.false_matches)
        self.auto_rate.append(metrics.auto_rate)
        self.open_exceptions.append(metrics.open_exceptions)
        self.throughput.append(metrics.throughput_rps)

    def as_dict(self) -> RowSummary:
        mean = statistics.fmean
        return RowSummary(
            label=self.label,
            runs=self.runs,
            records=self.records,
            precision=round(mean(self.precision), 4) if self.precision else None,
            recall=round(mean(self.recall), 4) if self.recall else None,
            false_matches_total=sum(self.false_matches),
            false_matches_worst=max(self.false_matches) if self.false_matches else 0,
            auto_rate=round(mean(self.auto_rate), 4),
            open_exceptions_mean=round(mean(self.open_exceptions), 1),
            throughput_rps_median=round(statistics.median(self.throughput), 1),
        )


def _blank(label: str) -> Row:
    return Row(label, 0, 0, [], [], [], [], [], [])


def _score_one(seed: int, size: int, corruption: str | None) -> RunMetrics:
    corpus, generated_truth = generate_corpus(seed, size)
    truth: Truth | None = generated_truth
    if corruption is not None:
        spec = parse_mutation(corruption)
        if isinstance(spec, Err):
            raise SystemExit(f"bad corruption {corruption!r}: {spec.reason}")
        corpus, truth = apply_mutations(corpus, truth, [spec.value], seed=seed)
    start = time.perf_counter()
    result = match(corpus)
    return score_run(corpus, result, truth, time.perf_counter() - start)


def sweep() -> list[Row]:
    assert not (set(HELD_OUT_SEEDS) & set(GOLDEN_SEEDS)), "held-out seeds must not overlap the golden set"
    rows: list[Row] = []

    for size in SIZES:
        row = _blank(f"clean · {size} records")
        for seed in HELD_OUT_SEEDS:
            row.add(_score_one(seed, size, None))
        rows.append(row)

    for corruption in CORRUPTIONS:
        row = _blank(f"corrupted · {corruption}")
        for seed in HELD_OUT_SEEDS:
            row.add(_score_one(seed, SIZES[0], corruption))
        rows.append(row)

    return rows


def _fmt(value: float | None, digits: int = 3) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def print_markdown(rows: list[Row]) -> None:
    dicts = [row.as_dict() for row in rows]
    print(f"| {'Corpus':<34} | Runs | Records | Precision | Recall | False matches | Auto | Exceptions | Throughput |")
    print(f"|{'-' * 36}|-----:|--------:|----------:|-------:|--------------:|-----:|-----------:|-----------:|")
    for d in dicts:
        print(
            f"| {d['label']:<34} | {d['runs']:>4} | {d['records']:>7,} | "
            f"{_fmt(d['precision'])!s:>9} | {_fmt(d['recall'])!s:>6} | "
            f"{d['false_matches_total']:>13} | {d['auto_rate']:.3f} | "
            f"{d['open_exceptions_mean']:>10.1f} | {d['throughput_rps_median']:>7,.0f}/s |"
        )
    total_false = sum(d["false_matches_total"] for d in dicts)
    total_runs = sum(d["runs"] for d in dicts)
    total_records = sum(d["records"] for d in dicts)
    print()
    print(f"{total_runs} runs · {total_records:,} records · {total_false} false matches across every run.")
    # Said plainly, because a throughput figure that quietly includes or
    # excludes the slow parts is not a measurement, it is advertising.
    print("Throughput times engine.match() alone: no ingestion, no LLM triage, no database.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit the same numbers as JSON")
    args = parser.parse_args()
    rows = sweep()
    if args.json:
        print(json.dumps([row.as_dict() for row in rows], indent=2))
    else:
        print_markdown(rows)


if __name__ == "__main__":
    main()
