import pytest

from contracts.enums import EXCEPTION_METADATA, ExceptionCode


@pytest.mark.parametrize("code", list(ExceptionCode))
def test_every_exception_code_has_complete_metadata(code: ExceptionCode) -> None:
    meta = EXCEPTION_METADATA[code]
    assert meta.label.strip()
    assert meta.severity in (1, 2, 3)
    assert meta.suggested_action.strip()


def test_no_exception_code_missing_from_metadata() -> None:
    assert set(EXCEPTION_METADATA.keys()) == set(ExceptionCode)
