"""Phase 13 verification.

Two properties matter and they pull in opposite directions: a mutation has to
actually break something (or it is theatre), and truth has to stay correct
while it does (or the break stops being measurable). Every test below is one
half of that pair.
"""

import pytest

from contracts.corpus import Corpus
from contracts.enums import ExceptionCode, MutationKind
from datagen.generator import generate_corpus
from datagen.models import UNMATCHABLE, Truth
from datagen.mutations import MutationSpec, apply_mutations, format_mutation, parse_mutation
from datagen.serialize import truth_to_dict
from engine.pipeline import match
from money.result import Err, Ok
from scripts.eval import score_run

SEED = 1001
SIZE = 150

# What each corruption must surface as. These are the codes a finance lead
# would expect to read, not merely the codes the engine happens to emit: a
# deleted bank credit is "missing in bank", not a generic mismatch.
EXPECTED_CODE = {
    MutationKind.DUPLICATE_PAYMENT: ExceptionCode.DUPLICATE_CANDIDATE,
    MutationKind.SHIFT_DATE: ExceptionCode.DATE_OUTSIDE_WINDOW,
    MutationKind.ALTER_AMOUNT: ExceptionCode.AMT_MISMATCH_UNEXPLAINED,
    MutationKind.DELETE_BANK_LINE: ExceptionCode.MISSING_IN_BANK,
    MutationKind.INJECT_UNRELATED_CREDIT: ExceptionCode.UNIDENTIFIED_CREDIT,
    MutationKind.SCRAMBLE_NARRATION: ExceptionCode.MISSING_IN_BANK,
    MutationKind.SPLIT_PAYMENT: ExceptionCode.AMT_MISMATCH_UNEXPLAINED,
}

ALL_KINDS = list(MutationKind)


def _corpus_keys(corpus: Corpus) -> set[str]:
    return (
        {f"invoice:{i.id}" for i in corpus.invoices}
        | {f"payment:{p.id}" for p in corpus.payments}
        | {f"settlement:{s.id}" for s in corpus.settlements}
        | {f"bank_line:{b.id}" for b in corpus.bank_lines}
    )


def _assert_truth_internally_consistent(corpus: Corpus, truth: Truth) -> None:
    """Truth after a mutation has to obey every rule it obeyed before it: no
    record it does not know about, no record it knows about that is gone, and
    no group membership that points at a record the group does not list."""
    assert _corpus_keys(corpus) == set(truth.record_group.keys())

    referenced = set(truth.record_group.values()) - {UNMATCHABLE}
    assert referenced <= set(truth.groups.keys())

    for group in truth.groups.values():
        for invoice_id in group.invoice_ids:
            assert truth.record_group[f"invoice:{invoice_id}"] == group.id
        for payment_id in group.payment_ids:
            assert truth.record_group[f"payment:{payment_id}"] == group.id
        if group.settlement_id is not None:
            assert truth.record_group[f"settlement:{group.settlement_id}"] == group.id
        if group.bank_line_id is not None:
            assert truth.record_group[f"bank_line:{group.bank_line_id}"] == group.id


class TestSpecParsing:
    def test_bare_kind_parses_with_defaults(self) -> None:
        result = parse_mutation("duplicate_payment")
        assert isinstance(result, Ok)
        assert result.value.kind == MutationKind.DUPLICATE_PAYMENT

    def test_argument_is_read_for_the_kinds_that_take_one(self) -> None:
        shift = parse_mutation("shift_date:60")
        altered = parse_mutation("alter_amount:-250000")
        assert isinstance(shift, Ok) and shift.value.days == 60
        assert isinstance(altered, Ok) and altered.value.amount == -250000

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not_a_mutation",
            "shift_date:soon",
            "alter_amount:0",
            "duplicate_payment:3",
        ],
    )
    def test_bad_specs_come_back_as_errors_never_exceptions(self, raw: str) -> None:
        assert isinstance(parse_mutation(raw), Err)

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_spec_round_trips_through_its_string_form(self, kind: MutationKind) -> None:
        spec = MutationSpec(kind=kind)
        reparsed = parse_mutation(format_mutation(spec))
        assert isinstance(reparsed, Ok)
        assert reparsed.value == spec


class TestPerMutationBehaviour:
    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_expected_exception_code_appears(self, kind: MutationKind) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        clean = match(corpus)
        mutated, _ = apply_mutations(corpus, truth, [MutationSpec(kind=kind)], seed=SEED)
        after = match(mutated)

        codes = sorted({exc.code.value for exc in after.exceptions})
        assert EXPECTED_CODE[kind] in {exc.code for exc in after.exceptions}, (
            f"{kind.value} did not surface {EXPECTED_CODE[kind].value}; got {codes}"
        )
        # And the corruption has to have cost something. Without this a
        # mutation that happened to leave the books closable would still pass
        # on a code the clean corpus was already emitting elsewhere.
        assert len(after.exceptions) > len(clean.exceptions), f"{kind.value} opened nothing"

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_truth_stays_internally_consistent(self, kind: MutationKind) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(corpus, truth, [MutationSpec(kind=kind)], seed=SEED)
        assert mutated_truth is not None
        _assert_truth_internally_consistent(mutated, mutated_truth)

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_false_matches_stay_at_zero(self, kind: MutationKind) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(corpus, truth, [MutationSpec(kind=kind)], seed=SEED)
        metrics = score_run(mutated, match(mutated), mutated_truth, elapsed_seconds=0.01)
        assert metrics.false_matches == 0, f"{kind.value} produced a false match"

    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_the_corpus_actually_changed(self, kind: MutationKind) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, _ = apply_mutations(corpus, truth, [MutationSpec(kind=kind)], seed=SEED)
        assert mutated != corpus, f"{kind.value} left the corpus untouched"


class TestTruthLockstep:
    def test_added_records_are_marked_unmatchable(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(
            corpus,
            truth,
            [
                MutationSpec(kind=MutationKind.DUPLICATE_PAYMENT),
                MutationSpec(kind=MutationKind.INJECT_UNRELATED_CREDIT),
            ],
            seed=SEED,
        )
        assert mutated_truth is not None
        added = _corpus_keys(mutated) - _corpus_keys(corpus)
        assert len(added) == 2
        for key in added:
            assert mutated_truth.record_group[key] == UNMATCHABLE

    def test_deleting_a_bank_line_clears_it_from_its_group_not_the_whole_group(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(
            corpus, truth, [MutationSpec(kind=MutationKind.DELETE_BANK_LINE)], seed=SEED
        )
        assert mutated_truth is not None
        removed = _corpus_keys(corpus) - _corpus_keys(mutated)
        assert len(removed) == 1
        # The commercial event still happened; only the credit is gone.
        assert len(mutated_truth.groups) == len(truth.groups)
        assert sum(1 for g in mutated_truth.groups.values() if g.bank_line_id is None) == 1

    def test_corruption_in_place_leaves_truth_alone(self) -> None:
        """A scrambled narration hides a link; it does not dissolve one. If
        truth moved here the engine would get credit for leaving open
        something truth had already conceded was unmatchable."""
        corpus, truth = generate_corpus(SEED, SIZE)
        _, mutated_truth = apply_mutations(
            corpus, truth, [MutationSpec(kind=MutationKind.SCRAMBLE_NARRATION)], seed=SEED
        )
        assert mutated_truth is not None
        assert mutated_truth.record_group == truth.record_group
        assert mutated_truth.groups == truth.groups

    def test_a_scrambled_narration_is_scored_as_a_miss_not_a_pass(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(
            corpus, truth, [MutationSpec(kind=MutationKind.SCRAMBLE_NARRATION)], seed=SEED
        )
        clean = score_run(corpus, match(corpus), truth, elapsed_seconds=0.01)
        after = score_run(mutated, match(mutated), mutated_truth, elapsed_seconds=0.01)
        assert after.recall is not None and clean.recall is not None
        assert after.recall < clean.recall


class TestDeterminism:
    def test_same_specs_and_seed_replay_exactly(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        specs = [MutationSpec(kind=k) for k in ALL_KINDS]
        first_corpus, first_truth = apply_mutations(corpus, truth, specs, seed=7)
        second_corpus, second_truth = apply_mutations(corpus, truth, specs, seed=7)
        assert first_corpus == second_corpus
        assert first_truth is not None and second_truth is not None
        assert truth_to_dict(first_truth) == truth_to_dict(second_truth)

    def test_empty_spec_list_is_the_identity(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        same_corpus, same_truth = apply_mutations(corpus, truth, [], seed=SEED)
        assert same_corpus is corpus
        assert same_truth is truth

    def test_a_dataset_without_truth_still_mutates(self) -> None:
        corpus, _ = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(
            corpus, None, [MutationSpec(kind=MutationKind.INJECT_UNRELATED_CREDIT)], seed=SEED
        )
        assert mutated_truth is None
        assert len(mutated.bank_lines) == len(corpus.bank_lines) + 1


class TestChained:
    def test_five_mutations_in_sequence_keep_the_books_balanced(self) -> None:
        """The plan's chained case. Every record must still land in exactly one
        of matched or excepted -- corruption may open items, never lose them."""
        corpus, truth = generate_corpus(SEED, SIZE)
        specs = [
            MutationSpec(kind=MutationKind.DUPLICATE_PAYMENT),
            MutationSpec(kind=MutationKind.SHIFT_DATE),
            MutationSpec(kind=MutationKind.ALTER_AMOUNT),
            MutationSpec(kind=MutationKind.DELETE_BANK_LINE),
            MutationSpec(kind=MutationKind.INJECT_UNRELATED_CREDIT),
        ]
        mutated, mutated_truth = apply_mutations(corpus, truth, specs, seed=SEED)
        assert mutated_truth is not None
        _assert_truth_internally_consistent(mutated, mutated_truth)

        result = match(mutated)
        grouped = (
            {f"invoice:{i}" for g in result.groups for i in g.invoice_ids}
            | {f"payment:{p}" for g in result.groups for p in g.payment_ids}
            | {f"settlement:{g.settlement_id}" for g in result.groups if g.settlement_id}
            | {f"bank_line:{g.bank_line_id}" for g in result.groups if g.bank_line_id}
        )
        excepted = {f"{r.kind}:{r.id}" for exc in result.exceptions for r in exc.records}
        every_record = _corpus_keys(mutated)

        assert grouped & excepted == set(), "a record was both matched and excepted"
        assert every_record - (grouped | excepted) == set(), "a record vanished from the accounting"

        metrics = score_run(mutated, result, mutated_truth, elapsed_seconds=0.01)
        assert metrics.false_matches == 0

    def test_all_seven_at_once_still_holds_together(self) -> None:
        corpus, truth = generate_corpus(SEED, SIZE)
        mutated, mutated_truth = apply_mutations(corpus, truth, [MutationSpec(kind=k) for k in ALL_KINDS], seed=SEED)
        assert mutated_truth is not None
        _assert_truth_internally_consistent(mutated, mutated_truth)
        metrics = score_run(mutated, match(mutated), mutated_truth, elapsed_seconds=0.01)
        assert metrics.false_matches == 0
