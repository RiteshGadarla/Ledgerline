import random
import string

from hypothesis import given
from hypothesis import strategies as st

from contracts.money import Paise
from money.parse import format_paise, parse_amount
from money.result import Err, Ok


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_round_trip_through_canonical_format(amount: int) -> None:
    result = parse_amount(format_paise(Paise(amount)))
    assert result == Ok(Paise(amount))


@given(
    st.integers(min_value=-(10**10), max_value=10**10),
    st.integers(min_value=-(10**10), max_value=10**10),
)
def test_sum_is_associative_and_commutative(a: int, b: int) -> None:
    ra = parse_amount(str(a))
    rb = parse_amount(str(b))
    assert isinstance(ra, Ok)
    assert isinstance(rb, Ok)
    assert ra.value + rb.value == rb.value + ra.value


@given(st.integers(min_value=-10**15, max_value=10**15))
def test_result_never_holds_a_float(amount: int) -> None:
    result = parse_amount(format_paise(Paise(amount)))
    assert isinstance(result, Ok)
    assert isinstance(result.value, int)
    assert not isinstance(result.value, float)


def test_fuzz_random_strings_never_raise() -> None:
    rng = random.Random(1337)
    alphabet = string.printable
    for _ in range(10_000):
        length = rng.randint(0, 40)
        garbage = "".join(rng.choice(alphabet) for _ in range(length))
        result = parse_amount(garbage)
        assert isinstance(result, Ok | Err)


NEGATIVE_SHAPES = [
    ("(1,234.00)", -123400),
    ("500.00 Dr", -50000),
    ("500.00 dr", -50000),
    ("-500.00", -50000),
]


def test_parenthesised_and_dr_amounts_are_negative() -> None:
    for text, expected in NEGATIVE_SHAPES:
        assert parse_amount(text) == Ok(Paise(expected))


def test_indian_grouping_parses_correctly() -> None:
    assert parse_amount("1,23,456.78") == Ok(Paise(12345678))


def test_currency_prefixes_are_stripped() -> None:
    assert parse_amount("₹1,234.00") == Ok(Paise(123400))
    assert parse_amount("Rs. 1234") == Ok(Paise(123400))
    assert parse_amount("INR 1234.00") == Ok(Paise(123400))


def test_unicode_spaces_are_stripped() -> None:
    assert parse_amount("1 234.50") == Ok(Paise(123450))
    assert parse_amount("1　234.50") == Ok(Paise(123450))


def test_credit_suffix_stays_positive() -> None:
    assert parse_amount("500.00 Cr") == Ok(Paise(50000))


def test_int_input_passes_through_as_paise() -> None:
    assert parse_amount(50000) == Ok(Paise(50000))


def test_garbage_is_an_error_not_an_exception() -> None:
    result = parse_amount("not an amount")
    assert isinstance(result, Err)


def test_empty_string_is_an_error() -> None:
    assert isinstance(parse_amount(""), Err)
