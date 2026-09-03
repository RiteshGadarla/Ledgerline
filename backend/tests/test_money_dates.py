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


def test_timestamped_dates_keep_their_calendar_date() -> None:
    # What a real export writes: gateway files date a capture to the minute or
    # the second, and the row is still a row.
    assert parse_date("2026-08-04 00:00") == Ok(date(2026, 8, 4))
    assert parse_date("2026-08-04 10:23:45") == Ok(date(2026, 8, 4))
    assert parse_date("2026-08-04 09:15 PM") == Ok(date(2026, 8, 4))
    assert parse_date("04/08/2026 10:23") == Ok(date(2026, 8, 4))
    assert parse_date("05-Jan-24 09:15:30") == Ok(date(2024, 1, 5))


def test_iso_instants_are_read_in_ist() -> None:
    # 21:00 UTC is 02:30 the next morning in IST, and the books are kept in
    # IST: the row belongs to the 5th.
    assert parse_date("2026-08-04T21:00:00Z") == Ok(date(2026, 8, 5))
    assert parse_date("2026-08-04T10:23:45+05:30") == Ok(date(2026, 8, 4))
    # No offset is a local reading, so no shift is applied.
    assert parse_date("2026-08-04T10:23:45") == Ok(date(2026, 8, 4))


def test_a_time_alone_is_still_not_a_date() -> None:
    assert isinstance(parse_date("10:23:45"), Err)
    assert isinstance(parse_date("10:23"), Err)


def test_an_unreadable_time_does_not_cost_a_readable_date() -> None:
    # The time of day is noise to a calendar date, so a corrupt one is
    # dropped with the rest of it rather than taking a good row down.
    assert parse_date("2026-08-04 25:99") == Ok(date(2026, 8, 4))
