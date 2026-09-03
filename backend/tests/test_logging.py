"""The logging layer: one JSON object per line, on stdout and in a rotating
file, carrying whatever context the caller attached."""

import json
import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest

from app.logging_config import JsonFormatter, configure_logging
from app.settings import get_settings


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the process's logging at a scratch directory, and put the real
    configuration back afterwards so a later test still logs where it should.

    Through the environment rather than by patching get_settings: it is
    lru_cached, so the cache -- not the name -- is what a caller reads.
    """
    directory = tmp_path / "logs"
    monkeypatch.setenv("LOG_DIR", str(directory))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    previous = list(logging.getLogger().handlers)
    try:
        yield directory
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
        logging.getLogger().handlers = previous
        get_settings.cache_clear()


def _formatted(record_kwargs: dict[str, object]) -> dict[str, Any]:
    record = logging.LogRecord(
        name="ledgerline.test", level=logging.INFO, pathname=__file__, lineno=1, msg="hello", args=(), exc_info=None
    )
    for key, value in record_kwargs.items():
        setattr(record, key, value)
    payload: dict[str, Any] = json.loads(JsonFormatter().format(record))
    return payload


def test_every_line_is_json_carrying_a_timestamp_level_and_logger() -> None:
    payload = _formatted({})

    assert payload["level"] == "INFO"
    assert payload["logger"] == "ledgerline.test"
    assert payload["message"] == "hello"
    # A log file whose lines carry no time is a list of things that happened
    # in an unknown order.
    assert payload["time"].endswith("+00:00")


def test_context_passed_as_extra_survives_into_the_line() -> None:
    """run_id, user_id and the request path all reach the formatter this way;
    a fixed key list would drop every one of them."""
    payload = _formatted({"run_id": "RUN-1", "user_id": "u1", "path": "/runs"})

    assert payload["run_id"] == "RUN-1"
    assert payload["user_id"] == "u1"
    assert payload["path"] == "/runs"


def test_an_unserialisable_extra_does_not_take_the_line_down() -> None:
    payload = _formatted({"corpus": object()})

    assert payload["message"] == "hello"
    assert isinstance(payload["corpus"], str)


def test_configure_logging_writes_the_component_s_own_file(log_dir: Path) -> None:
    configure_logging("worker")

    logging.getLogger("ledgerline.worker").info("run_started", extra={"run_id": "RUN-7"})
    for handler in logging.getLogger().handlers:
        handler.flush()

    lines = (log_dir / "worker.log").read_text().strip().splitlines()
    written = json.loads(lines[-1])
    assert written["message"] == "run_started"
    assert written["run_id"] == "RUN-7"


def test_libraries_that_bring_their_own_handlers_are_routed_through_the_root(log_dir: Path) -> None:
    """arq configures its own handler after importing the worker module, and
    uvicorn configures one before importing the app. Either way their records
    have to end up in the same file as everything else."""
    arq_logger = logging.getLogger("arq")
    arq_logger.handlers = [logging.StreamHandler()]
    arq_logger.propagate = False

    configure_logging("worker")

    assert arq_logger.handlers == []
    assert arq_logger.propagate is True

    arq_logger.info("job complete")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "job complete" in (log_dir / "worker.log").read_text()


def test_an_empty_log_dir_keeps_logging_to_stdout_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container case: something in front of the process already ships
    stdout, and a file on a disposable filesystem is only overhead."""
    monkeypatch.setenv("LOG_DIR", "")
    get_settings.cache_clear()
    previous = list(logging.getLogger().handlers)
    try:
        configure_logging("api")

        handlers = logging.getLogger().handlers
        assert len(handlers) == 1
        assert not isinstance(handlers[0], RotatingFileHandler)
    finally:
        for handler in logging.getLogger().handlers:
            handler.close()
        logging.getLogger().handlers = previous
        get_settings.cache_clear()
