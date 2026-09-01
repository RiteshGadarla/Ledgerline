import json
from pathlib import Path

from datagen.difficulty import DifficultyClass
from datagen.generator import generate_corpus
from datagen.models import UNMATCHABLE
from datagen.serialize import truth_to_dict, write_corpus

SEEDS = [1001, 1002, 1003]
SIZES = [50, 150, 500]


def _all_record_keys(corpus) -> set[str]:  # type: ignore[no-untyped-def]
    keys: set[str] = set()
    keys.update(f"invoice:{i.id}" for i in corpus.invoices)
    keys.update(f"payment:{p.id}" for p in corpus.payments)
    keys.update(f"settlement:{s.id}" for s in corpus.settlements)
    keys.update(f"bank_line:{b.id}" for b in corpus.bank_lines)
    return keys


def test_settlement_accounting_invariant_holds_exactly() -> None:
    for seed in SEEDS:
        for size in SIZES:
            corpus, _ = generate_corpus(seed, size)
            payments_by_id = {p.id: p for p in corpus.payments}
            for settlement in corpus.settlements:
                members = [payments_by_id[pid] for pid in settlement.payment_ids]
                gross_total = sum(p.gross for p in members)
                refunds_total = sum(p.gross for p in members if p.status in ("refunded", "disputed"))
                recomputed = gross_total - settlement.fees - settlement.tax - refunds_total + settlement.adjustments
                assert recomputed == settlement.payout, f"seed={seed} size={size} settlement={settlement.id}"


def test_determinism_same_seed_same_size_is_byte_identical() -> None:
    for seed in SEEDS:
        corpus_a, truth_a = generate_corpus(seed, 150)
        corpus_b, truth_b = generate_corpus(seed, 150)
        assert corpus_a == corpus_b
        assert truth_to_dict(truth_a) == truth_to_dict(truth_b)


def test_different_seeds_produce_different_corpora() -> None:
    corpus_a, _ = generate_corpus(1001, 150)
    corpus_b, _ = generate_corpus(1002, 150)
    assert corpus_a != corpus_b


def test_every_difficulty_class_present_at_default_seed() -> None:
    _, truth = generate_corpus(1001, 150)
    counts = truth.class_counts()
    for difficulty in DifficultyClass:
        assert counts.get(difficulty, 0) >= 1, f"missing class: {difficulty}"


def test_genuinely_unmatchable_records_exist() -> None:
    _, truth = generate_corpus(1001, 150)
    unmatchable = [key for key, group in truth.record_group.items() if group == UNMATCHABLE]
    assert len(unmatchable) > 0


def test_truth_integrity_every_id_round_trips() -> None:
    for seed in SEEDS:
        corpus, truth = generate_corpus(seed, 150)
        corpus_keys = _all_record_keys(corpus)
        truth_keys = set(truth.record_group.keys())
        assert corpus_keys == truth_keys, f"seed={seed}: mismatch {corpus_keys ^ truth_keys}"

        referenced_groups = set(truth.record_group.values()) - {UNMATCHABLE}
        assert referenced_groups <= set(truth.groups.keys())

        for group in truth.groups.values():
            for invoice_id in group.invoice_ids:
                assert truth.record_group[f"invoice:{invoice_id}"] == group.id
            for payment_id in group.payment_ids:
                assert truth.record_group[f"payment:{payment_id}"] == group.id


def test_sizes_scale_record_count() -> None:
    for size in SIZES:
        corpus, _ = generate_corpus(1001, size)
        total = len(corpus.invoices) + len(corpus.payments)
        assert total >= size


def test_batches_within_configured_bounds() -> None:
    corpus, _ = generate_corpus(1001, 500)
    for settlement in corpus.settlements:
        assert len(settlement.payment_ids) >= 1


def test_write_corpus_emits_four_csvs_and_truth_json(tmp_path: Path) -> None:
    corpus, truth = generate_corpus(1001, 150)
    write_corpus(corpus, truth, tmp_path)

    for filename in ["invoices.csv", "payments.csv", "settlements.csv", "bank_lines.csv", "truth.json"]:
        assert (tmp_path / filename).exists()

    truth_on_disk = json.loads((tmp_path / "truth.json").read_text())
    assert truth_on_disk["record_group"] == truth.record_group
    invoices_csv = (tmp_path / "invoices.csv").read_text()
    assert invoices_csv.count("\n") == len(corpus.invoices) + 1  # header + rows


class TestVariance:
    """The corpus has to be uneven in the ways real books are uneven. A
    generator that drifts back toward uniform amounts, one fee rate and one
    narration format still passes every invariant above while quietly making
    the engine untested, so the spread itself is asserted."""

    def test_amounts_are_heavy_tailed_not_uniform(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        amounts = sorted(int(i.amount) for i in corpus.invoices)
        median = amounts[len(amounts) // 2]
        largest = amounts[-1]
        # A uniform draw puts the median near the midpoint; a real invoice book
        # has a mass of small values under a long tail.
        assert largest > median * 20, f"tail too short: median={median} max={largest}"

    def test_amounts_cluster_on_round_rupee_figures(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        round_hundreds = sum(1 for i in corpus.invoices if int(i.amount) % 10_000 == 0)
        assert round_hundreds > len(corpus.invoices) * 0.2, "no round-number clustering"

    def test_fee_rates_differ_by_method_including_zero_mdr_upi(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        rates_by_method: dict[str, set[int]] = {}
        for payment in corpus.payments:
            if payment.gross == 0:
                continue
            bps = round(int(payment.fee) * 10_000 / int(payment.gross))
            rates_by_method.setdefault(payment.method, set()).add(bps)

        assert len(rates_by_method) >= 3, "every payment used the same method"
        assert rates_by_method.get("upi") == {0}, "UPI is zero-MDR in India; the corpus should say so"
        assert any(bps > 100 for rates in rates_by_method.values() for bps in rates)

    def test_bank_narrations_come_in_more_than_one_house_style(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        # Compare shapes, not contents: strip the digits that differ per row.
        shapes = {"".join(c for c in b.narration if not c.isdigit()) for b in corpus.bank_lines}
        assert len(shapes) >= 5, f"only {len(shapes)} narration shapes"

    def test_utrs_come_in_both_numeric_and_bank_prefixed_shapes(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        utrs = [s.utr for s in corpus.settlements if s.utr]
        assert any(u.isdigit() for u in utrs)
        assert any(not u.isdigit() for u in utrs), "no alphanumeric UTRs; a digits-only matcher would pass"

    def test_settlement_windows_and_credit_lag_both_vary(self) -> None:
        corpus, truth = generate_corpus(1001, 500)
        payments_by_id = {p.id: p for p in corpus.payments}
        settlements_by_id = {s.id: s for s in corpus.settlements}
        bank_lines_by_id = {b.id: b for b in corpus.bank_lines}

        windows = set()
        for settlement in corpus.settlements:
            last_capture = max(payments_by_id[pid].captured_at.date() for pid in settlement.payment_ids)
            windows.add((settlement.settled_at - last_capture).days)
        assert len(windows) >= 3, f"settlement window never moved: {windows}"

        # And the credit itself posts on the settlement date or a little after,
        # which is what the engine's credit window exists to tolerate.
        lags = {
            (bank_lines_by_id[group.bank_line_id].value_date - settlements_by_id[group.settlement_id].settled_at).days
            for group in truth.groups.values()
            if group.bank_line_id and group.settlement_id
        }
        assert len(lags) >= 2, f"every credit posted on exactly the settlement date: {lags}"
        assert min(lags) >= 0, "a credit posted before its own settlement"

    def test_capture_times_spread_across_the_clock(self) -> None:
        corpus, _ = generate_corpus(1001, 500)
        hours = {p.captured_at.hour for p in corpus.payments}
        assert len(hours) >= 10, "captures all land in the same few hours"
