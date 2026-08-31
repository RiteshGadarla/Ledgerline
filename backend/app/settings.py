from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    gemini_api_key: str | None = None
    database_url: str | None = None
    redis_url: str | None = None
    session_cookie_name: str = "ledgerline_session"


@lru_cache
def get_settings() -> Settings:
    return Settings()
