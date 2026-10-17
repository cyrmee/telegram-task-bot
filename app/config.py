"""Settings from the environment (.env in dev)."""
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = ""

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
