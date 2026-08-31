import random
from datetime import date

from money.dates import parse_date
from money.result import Err, Ok


def test_dd_mm_yyyy_slash() -> None:
    assert parse_date("05/01/2024") == Ok(date(2024, 1, 5))


def test_dd_mmm_yy() -> None:
    assert parse_date("05-Jan-24") == Ok(date(2024, 1, 5))
    assert parse_date("05-JAN-24") == Ok(date(2024, 1, 5))


def test_yyyy_mm_dd() -> None:
    assert parse_date("2024-01-05") == Ok(date(2024, 1, 5))


def test_excel_serial() -> None:
    # Excel serial 45292 is 2024-01-01 under the standard (buggy) Excel epoch.
    assert parse_date(45292) == Ok(date(2024, 1, 1))


def test_invalid_calendar_date_is_an_error() -> None:
    assert isinstance(parse_date("31/02/2024"), Err)


def test_unrecognised_shape_is_an_error() -> None:
    assert isinstance(parse_date("not a date"), Err)


def test_empty_string_is_an_error() -> None:
    assert isinstance(parse_date(""), Err)


def test_fuzz_random_strings_never_raise() -> None:
    rng = random.Random(2024)
    for _ in range(5_000):
        length = rng.randint(0, 20)
        garbage = "".join(chr(rng.randint(32, 126)) for _ in range(length))
        result = parse_date(garbage)
        assert isinstance(result, Ok | Err)
