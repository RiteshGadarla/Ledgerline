from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    gemini_api_key: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    session_cookie_name: str = "ledgerline_session"

    log_level: str = "INFO"
    # Where each process writes its own rotating log file, relative to the
    # working directory it was started from. Set LOG_DIR empty to log to
    # stdout only, which is what you want behind a collector that already
    # ships it somewhere.
    log_dir: str = "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
