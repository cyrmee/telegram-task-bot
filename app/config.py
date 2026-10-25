"""Settings from the environment (.env in dev)."""
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""
    log_level: str = "INFO"
    log_json: bool = True

    # Pre-v1 single bot (app/bot/legacy). Off by default: it only works
    # against the legacy schema. B1's BotManager replaces it.
    legacy_bot_enabled: bool = False
    telegram_bot_token: str = ""

    # Per-workspace bots (app/bot, B1) and the notification worker (B4).
    # Off in the test suite unless a test turns it on.
    bot_runtime_enabled: bool = True
    # Fernet key for workspace_bots.token_encrypted.
    token_encryption_key: str = ""
    # Public HTTPS origin Telegram posts to: <public_base_url>/telegram/<bot_id>. Empty = no webhooks.
    public_base_url: str = ""

    # Error reporting (D3). Off unless SENTRY_DSN is set. The release defaults to
    # Render's RENDER_GIT_COMMIT.
    sentry_dsn: str = ""
    sentry_environment: str = "development"
    sentry_release: str = ""
    sentry_traces_sample_rate: float = 0.0

    # Per-IP request limits (app/ratelimit.py). Tests turn it off unless they test it.
    rate_limit_enabled: bool = True

    # better-auth JWT plugin on the dashboard (docs/api-v1.md §2)
    auth_jwks_url: str = "http://localhost:3000/api/auth/jwks"
    auth_jwt_issuer: str = "http://localhost:3000"
    auth_jwt_audience: str = "nest-api"
    auth_jwks_cache_seconds: int = 300
    auth_leeway_seconds: int = 30

    # CORS_ORIGINS is comma-separated.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
