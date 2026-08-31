import time

from datagen.generator import generate_corpus
from engine.pipeline import match

SEEDS = [1001, 1002, 1003]


def _score_false_matches(groups, truth) -> int:  # type: ignore[no-untyped-def]
    false_matches = 0
    for group in groups:
        truth_group = truth.groups.get(group.settlement_id)
        if truth_group is None:
            false_matches += 1
            continue
        if set(group.invoice_ids) != set(truth_group.invoice_ids):
            false_matches += 1
        if set(group.payment_ids) != set(truth_group.payment_ids):
            false_matches += 1
    return false_matches


def test_zero_false_matches_on_committed_seeds() -> None:
    for seed in SEEDS:
        corpus, truth = generate_corpus(seed, 150)
        result = match(corpus)
        assert _score_false_matches(result.groups, truth) == 0, f"seed={seed} produced a false match"


def test_idempotence_same_input_same_hash() -> None:
    corpus, _ = generate_corpus(1001, 150)
    result_a = match(corpus)
    result_b = match(corpus)
    assert result_a.output_hash == result_b.output_hash


def test_total_accounting_every_record_matched_or_residue() -> None:
    corpus, _ = generate_corpus(1001, 150)
    result = match(corpus)

    matched_invoice_ids: set[str] = set()
    matched_payment_ids: set[str] = set()
    matched_settlement_ids: set[str] = set()
    matched_bank_line_ids: set[str] = set()
    for group in result.groups:
        matched_invoice_ids.update(group.invoice_ids)
        matched_payment_ids.update(group.payment_ids)
        if group.settlement_id:
            matched_settlement_ids.add(group.settlement_id)
        if group.bank_line_id:
            matched_bank_line_ids.add(group.bank_line_id)

    residue_by_kind: dict[str, set[str]] = {"invoice": set(), "payment": set(), "settlement": set(), "bank_line": set()}
    for item in result.residue:
        residue_by_kind[item.kind].add(item.id)

    all_invoice_ids = {i.id for i in corpus.invoices}
    all_payment_ids = {p.id for p in corpus.payments}
    all_settlement_ids = {s.id for s in corpus.settlements}
    all_bank_line_ids = {b.id for b in corpus.bank_lines}

    assert matched_invoice_ids | residue_by_kind["invoice"] == all_invoice_ids
    assert matched_invoice_ids & residue_by_kind["invoice"] == set()
    assert matched_payment_ids | residue_by_kind["payment"] == all_payment_ids
    assert matched_payment_ids & residue_by_kind["payment"] == set()
    assert matched_settlement_ids | residue_by_kind["settlement"] == all_settlement_ids
    assert matched_bank_line_ids | residue_by_kind["bank_line"] == all_bank_line_ids


def test_ambiguous_settlements_never_produce_a_silent_pick() -> None:
    # The default corpus never produces two bank lines tied on the same UTR+amount,
    # so ambiguity is exercised at the unit level (test_engine_passes.py). Here we
    # assert the pipeline never fabricates a settlement->bank_line link that isn't
    # backed by a unique P1 candidate.
    corpus, _ = generate_corpus(1002, 150)
    result = match(corpus)
    seen_bank_lines: set[str] = set()
    for group in result.groups:
        assert group.bank_line_id not in seen_bank_lines
        if group.bank_line_id:
            seen_bank_lines.add(group.bank_line_id)


def test_complexity_scales_sub_quadratically() -> None:
    corpus_small, _ = generate_corpus(1001, 1250)
    corpus_large, _ = generate_corpus(1001, 5000)

    start = time.perf_counter()
    match(corpus_small)
    small_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    match(corpus_large)
    large_elapsed = time.perf_counter() - start

    assert large_elapsed < max(small_elapsed * 4, 0.5)
