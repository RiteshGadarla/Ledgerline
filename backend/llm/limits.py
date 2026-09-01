import os
from dataclasses import dataclass

from llm.models import BACKUP_MODEL, PRIMARY_MODEL


@dataclass(frozen=True)
class ModelLimits:
    rpm: int
    rpd: int
    tpm: int


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def load_model_limits() -> dict[str, ModelLimits]:
    """Per-model request ceilings, loaded from env so a quota change is a
    deploy, not a code change.

    Defaults are deliberately conservative. A free-tier key's real per-day
    ceiling has been observed far below the vendor's published figure, and
    the governor is what turns "over quota" into an instant, honest refusal
    instead of a slow request that fails upstream -- so when in doubt these
    should be set too low rather than too high.
    """
    return {
        PRIMARY_MODEL: ModelLimits(
            rpm=_int_env("GEMMA_PRIMARY_RPM", 10),
            rpd=_int_env("GEMMA_PRIMARY_RPD", 250),
            tpm=_int_env("GEMMA_PRIMARY_TPM", 250_000),
        ),
        BACKUP_MODEL: ModelLimits(
            rpm=_int_env("GEMMA_BACKUP_RPM", 15),
            rpd=_int_env("GEMMA_BACKUP_RPD", 1_000),
            tpm=_int_env("GEMMA_BACKUP_TPM", 250_000),
        ),
    }


DEFAULT_USER_DAILY_QUOTA = _int_env("LLM_USER_DAILY_QUOTA", 25)
