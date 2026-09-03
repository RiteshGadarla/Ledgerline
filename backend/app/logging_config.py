import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.settings import get_settings

# 10 MB per file, five kept: enough history to cover an incident, bounded
# hard enough that an unattended box cannot fill its disk with logs.
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUPS = 5

# Libraries that install handlers of their own and format to their own taste.
# Left alone, uvicorn's access log and arq's job log would print in two other
# formats and never reach the file at all, so they are stripped back to bare
# loggers and pointed at the root -- one stream, one format, everything in it.
_ADOPTED_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access", "arq", "arq.worker")

# Everything LogRecord sets on itself. Whatever is left over on a record was
# put there by a caller's `extra=`, and is worth keeping: `run_id`, `user_id`
# and the request `path` that app/errors.py attaches all arrive that way, and
# a formatter with a fixed key list silently drops them.
_RESERVED = frozenset(
    logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None).__dict__
) | {
    "asctime",
    "message",
    "taskName",
    # uvicorn attaches an ANSI-escaped copy of its own message to every
    # record. Useful to uvicorn's console formatter, noise in a JSON line.
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: greppable by eye, and `jq`-able when not."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # A log file without timestamps is a list of things that happened
            # in an unknown order. UTC, to match every other instant the API
            # emits (see frontend/lib/time.ts on why that is the storage form).
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update({key: value for key, value in record.__dict__.items() if key not in _RESERVED})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # default=str: a stray unserialisable value in `extra` must not take
        # the handler down and lose the line that was trying to report it.
        return json.dumps(payload, default=str)


def configure_logging(component: str = "api") -> None:
    """Point every logger in this process at stdout and at a rotating file.

    stdout is what a container runtime or systemd collects; the file is what
    you read when there is no collector in front of the process, which on a
    single box is the usual case. Set LOG_DIR empty to keep stdout only.

    `component` names the file, and each process gets its own -- api.log,
    worker.log. Not cosmetic: RotatingFileHandler renames the file it rolls,
    and two processes rolling one path lose each other's lines. Within a
    single process the handler is already safe.

    Safe to call more than once. The worker calls it twice on purpose: once
    at import, so anything logged during startup is captured, and again from
    arq's on_startup hook, because arq runs its own dictConfig *after*
    importing the worker module and would otherwise keep its job log to
    itself in its own format.
    """
    settings = get_settings()
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.log_dir:
        directory = Path(settings.log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                directory / f"{component}.log",
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUPS,
                encoding="utf-8",
            )
        )

    formatter = JsonFormatter()
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    # Release the previous file handle before dropping the reference, so a
    # second call cannot leak a descriptor onto a file nothing will close.
    for previous in root.handlers:
        previous.close()
    root.handlers = handlers
    root.setLevel(level)

    for name in _ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers = []
        adopted.propagate = True


__all__ = ["JsonFormatter", "configure_logging"]
