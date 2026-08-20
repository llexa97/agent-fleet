from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_FLEET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    product_name: str = "Agent Fleet"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./agent_fleet_test.db"
    redis_url: str | None = None
    public_url: str = "http://localhost:8000"
    web_origin: str = "http://localhost:5173"
    session_secret: str = "development-only-change-this-secret"
    bootstrap_token: str = "development-bootstrap-token"
    cookie_secure: bool = False
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    embedded_dispatcher: bool = True
    log_level: str = "INFO"
    log_json: bool = True
    worker_offline_after_seconds: int = Field(default=45, ge=10, le=3600)
    delivery_lease_seconds: int = Field(default=120, ge=10, le=3600)
    max_ws_message_bytes: int = Field(default=1_048_576, ge=16_384, le=16_777_216)
    dispatcher_poll_seconds: float = Field(default=0.5, ge=0.05, le=30)
    max_login_attempts_per_minute: int = Field(default=10, ge=1, le=1000)
    session_ttl_hours: int = Field(default=24, ge=1, le=720)

    @field_validator("trusted_hosts", mode="before")
    @classmethod
    def parse_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("session_secret")
    @classmethod
    def secure_session_secret(cls, value: str, info: object) -> str:
        # La vérification stricte de production est faite au démarrage, ce qui permet
        # aux commandes Alembic et aux tests de charger les métadonnées.
        return value

    def validate_runtime_security(self) -> None:
        if self.environment == "production":
            if len(self.session_secret) < 32 or "change" in self.session_secret.lower():
                raise RuntimeError(
                    "AGENT_FLEET_SESSION_SECRET doit être aléatoire et >= 32 caractères"
                )
            if len(self.bootstrap_token) < 24 or "change" in self.bootstrap_token.lower():
                raise RuntimeError("AGENT_FLEET_BOOTSTRAP_TOKEN doit être remplacé")
            if not self.cookie_secure:
                raise RuntimeError("AGENT_FLEET_COOKIE_SECURE doit être vrai en production")
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise RuntimeError("PostgreSQL est obligatoire en production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
