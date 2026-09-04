import time
from pathlib import Path

from contracts.corpus import Corpus
from contracts.models import ClassScore, MatchGroup, RunMetrics
from contracts.money import Paise
from datagen.generator import generate_corpus
from datagen.models import UNMATCHABLE, Truth
from engine.pipeline import MatchResult, match

GOLDEN_SEEDS = [1001, 1002, 1003]
GOLDEN_SIZE = 150
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "golden_metrics"


def run_and_score(seed: int, size: int) -> RunMetrics:
    corpus, truth = generate_corpus(seed, size)
    start = time.perf_counter()
    result = match(corpus)
    elapsed = time.perf_counter() - start
    return score_run(corpus, result, truth, elapsed)


def score_run(corpus: Corpus, result: MatchResult, truth: Truth | None, elapsed_seconds: float) -> RunMetrics:
    total_payments = len(corpus.payments)
    auto_payment_ids = {pid for g in result.groups if g.status == "auto" for pid in g.payment_ids}
    assisted_payment_ids = {pid for g in result.groups if g.status == "assisted" for pid in g.payment_ids}
    exceptioned_payment_ids = {r.id for e in result.exceptions for r in e.records if r.kind == "payment"}

    auto_rate = len(auto_payment_ids) / total_payments if total_payments else 0.0
    assist_rate = len(assisted_payment_ids) / total_payments if total_payments else 0.0
    open_rate = len(exceptioned_payment_ids) / total_payments if total_payments else 0.0

    precision: float | None = None
    recall: float | None = None
    false_matches: int | None = None
    by_class: dict[str, ClassScore] | None = None

    if truth is not None:
        true_positive = _count_true_positive_groups(result, truth)
        false_matches = len(result.groups) - true_positive
        precision = true_positive / len(result.groups) if result.groups else 1.0
        recall = true_positive / len(truth.groups) if truth.groups else 1.0
        by_class = _score_by_class(result, truth)

    records = len(corpus.invoices) + len(corpus.payments) + len(corpus.settlements) + len(corpus.bank_lines)
    throughput_rps = records / elapsed_seconds if elapsed_seconds > 0 else 0.0
    elapsed_ms = int(elapsed_seconds * 1000)
    amount_at_risk = Paise(sum(int(exc.amount_at_risk) for exc in result.exceptions))

    # What tied out, in money. The settlement's payout is the figure that
    # actually moved through the bank for a matched chain, so a group without
    # one contributes nothing here rather than being estimated from its parts
    # -- an impact number built on a guess would be worth less than no impact
    # number at all.
    settlements_by_id = {s.id: s for s in corpus.settlements}
    amount_cleared = Paise(
        sum(
            int(settlements_by_id[group.settlement_id].payout)
            for group in result.groups
            if group.settlement_id in settlements_by_id
        )
    )

    return RunMetrics(
        auto_rate=auto_rate,
        assist_rate=assist_rate,
        open_rate=open_rate,
        precision=precision,
        recall=recall,
        false_matches=false_matches,
        by_class=by_class,
        records=records,
        open_exceptions=len(result.exceptions),
        amount_at_risk=amount_at_risk,
        payments_total=total_payments,
        payments_auto=len(auto_payment_ids),
        payments_assisted=len(assisted_payment_ids),
        amount_cleared=amount_cleared,
        throughput_rps=throughput_rps,
        p50_ms=elapsed_ms,
        p95_ms=elapsed_ms,
        llm_requests=0,
        llm_tokens=0,
        llm_degraded=False,
        output_hash=result.output_hash,
    )


def _is_true_positive(group: MatchGroup, truth: Truth) -> bool:
    if group.settlement_id is None:
        return False
    truth_group = truth.groups.get(group.settlement_id)
    if truth_group is None:
        return False
    return set(group.invoice_ids) == set(truth_group.invoice_ids) and set(group.payment_ids) == set(
        truth_group.payment_ids
    )


def _count_true_positive_groups(result: MatchResult, truth: Truth) -> int:
    return sum(1 for group in result.groups if _is_true_positive(group, truth))


def _score_by_class(result: MatchResult, truth: Truth) -> dict[str, ClassScore]:
    """Per-class recall: a record's *correct* outcome depends on what truth says it is.

    For a class truth expects to resolve into a real group (clean, refund_in_batch,
    ...), success means it landed in a group that matches truth exactly. For a class
    truth marks unmatchable (duplicate_payment, unmatchable, unrelated_credit),
    success means it correctly stayed out of every produced group -- a false match
    there would be the opposite of success, not a lesser form of it.
    """
    grouped_invoice_ids = {iid for group in result.groups for iid in group.invoice_ids}
    grouped_bank_line_ids = {group.bank_line_id for group in result.groups if group.bank_line_id}
    true_positive_invoice_ids: set[str] = set()
    for group in result.groups:
        if _is_true_positive(group, truth):
            true_positive_invoice_ids.update(group.invoice_ids)

    counts: dict[str, int] = {}
    correct: dict[str, int] = {}
    for key, difficulty in truth.record_difficulty.items():
        kind, record_id = key.split(":", 1)
        if kind == "invoice":
            grouped, true_positive = grouped_invoice_ids, true_positive_invoice_ids
        elif kind == "bank_line":
            grouped, true_positive = grouped_bank_line_ids, grouped_bank_line_ids
        else:
            continue

        counts[difficulty.value] = counts.get(difficulty.value, 0) + 1
        expected_unmatchable = truth.record_group.get(key) == UNMATCHABLE
        is_correct = (record_id not in grouped) if expected_unmatchable else (record_id in true_positive)
        if is_correct:
            correct[difficulty.value] = correct.get(difficulty.value, 0) + 1

    return {
        cls: ClassScore(precision=None, recall=(correct.get(cls, 0) / count if count else None), count=count)
        for cls, count in counts.items()
    }


def _print_report(seed: int, metrics: RunMetrics) -> None:
    print(f"seed={seed}")
    print(f"  records          {metrics.records}")
    print(f"  auto_rate        {metrics.auto_rate:.4f}")
    print(f"  assist_rate      {metrics.assist_rate:.4f}")
    print(f"  open_rate        {metrics.open_rate:.4f}")
    print(f"  precision        {metrics.precision}")
    print(f"  recall           {metrics.recall}")
    print(f"  false_matches    {metrics.false_matches}")
    print(f"  open_exceptions  {metrics.open_exceptions}")
    print(f"  amount_at_risk   {metrics.amount_at_risk} paise")
    print(f"  throughput_rps   {metrics.throughput_rps:.1f}")
    print(f"  p50_ms / p95_ms  {metrics.p50_ms} / {metrics.p95_ms}")
    print(f"  llm_requests     {metrics.llm_requests}")
    print(f"  llm_tokens       {metrics.llm_tokens}")
    print(f"  llm_degraded     {metrics.llm_degraded}")
    print(f"  output_hash      {metrics.output_hash}")
    if metrics.by_class:
        print("  by_class:")
        for cls, score in sorted(metrics.by_class.items()):
            print(f"    {cls:<24} count={score.count:<4} recall={score.recall}")


def golden_path(seed: int) -> Path:
    return FIXTURES_DIR / f"seed_{seed}.json"


def write_golden_metrics() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for seed in GOLDEN_SEEDS:
        metrics = run_and_score(seed, GOLDEN_SIZE)
        golden_path(seed).write_text(metrics.model_dump_json(indent=2) + "\n")


def main() -> None:
    for seed in GOLDEN_SEEDS:
        metrics = run_and_score(seed, GOLDEN_SIZE)
        _print_report(seed, metrics)
        print()


if __name__ == "__main__":
    main()
