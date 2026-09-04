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


# Per-user calls per day, for one credential. Everything else in this module
# is charged per key and so widens on its own when a key is added; this one is
# keyed by user and would not, which made it the binding limit the moment a
# second key was configured. `user_daily_quota()` is what callers should use.
DEFAULT_USER_DAILY_QUOTA = _int_env("LLM_USER_DAILY_QUOTA", 25)


def user_daily_quota(key_count: int) -> int:
    """The per-user daily ceiling, scaled to the size of the key pool.

    The pool exists to buy headroom: three keys are three independent free
    tiers, and a flat per-user cap would hand that headroom straight back --
    a user still stopped at 25 calls with 750 model-days available. An
    explicit LLM_USER_DAILY_QUOTA is honoured as an absolute figure, because
    someone who sets it is naming the ceiling they want, not a per-key rate.
    """
    if os.environ.get("LLM_USER_DAILY_QUOTA"):
        return DEFAULT_USER_DAILY_QUOTA
    return DEFAULT_USER_DAILY_QUOTA * max(1, key_count)
