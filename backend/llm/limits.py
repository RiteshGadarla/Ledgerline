import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelLimits:
    rpm: int
    rpd: int
    tpm: int


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def load_model_limits() -> dict[str, ModelLimits]:
    """Free-tier limits, loaded from env so a quota change is a deploy, not a code change.

    Defaults are the worst published figures at the time this was written; verify
    the current numbers against Google's rate-limits page before deploy.
    """
    return {
        "gemini-2.5-flash": ModelLimits(
            rpm=_int_env("GEMINI_FLASH_RPM", 10),
            rpd=_int_env("GEMINI_FLASH_RPD", 250),
            tpm=_int_env("GEMINI_FLASH_TPM", 250_000),
        ),
        "gemini-2.5-flash-lite": ModelLimits(
            rpm=_int_env("GEMINI_FLASH_LITE_RPM", 15),
            rpd=_int_env("GEMINI_FLASH_LITE_RPD", 1_000),
            tpm=_int_env("GEMINI_FLASH_LITE_TPM", 250_000),
        ),
    }


DEFAULT_USER_DAILY_QUOTA = _int_env("LLM_USER_DAILY_QUOTA", 25)
