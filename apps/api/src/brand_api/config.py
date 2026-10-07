"""Application settings.

Precedence (highest first): explicit init kwargs > environment > .env file > config/<APP_ENV>.yaml
> field defaults. Secrets and infrastructure URLs come from the environment; tunable behaviour
(models, thresholds, prices) lives in the YAML profile so it can be reviewed in git.
"""

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from brand_api.llm.config import LLMSettings

REPO_ROOT = Path(__file__).resolve().parents[4]
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me"  # noqa: S105 - rejected in prod
DEV_MCP_SECRET = "dev-only-insecure-mcp-secret-change-me"  # noqa: S105 - rejected in prod
MIN_PROD_SECRET_LENGTH = 32


class AppEnv(StrEnum):
    LOCAL = "local"
    PROD = "prod"
    TEST = "test"


def _resolve_app_env() -> AppEnv:
    raw = os.environ.get("APP_ENV") or dotenv_values(".env").get("APP_ENV") or AppEnv.LOCAL
    return AppEnv(raw)


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", REPO_ROOT / "config"))


class AgentSettings(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=20)
    tool_timeout_s: float = Field(default=30, gt=0)
    generation_timeout_s: float = Field(default=180, gt=0)  # includes 1-2 sampling rounds
    history_limit: int = Field(default=30, ge=2)
    max_tool_result_chars: int = Field(default=6000, ge=500)
    lock_ttl_s: int = Field(default=300, gt=0)
    # Answer confidence = w * retrieval + (1 - w) * model self-assessment.
    confidence_weight: float = Field(default=0.6, ge=0, le=1)
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    app_env: AppEnv = AppEnv.LOCAL
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql://brand:brand@localhost:5433/brand"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_ttl_minutes: int = Field(default=12 * 60, gt=0)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    allow_registration: bool = True
    # Demo mode: seed demo accounts and allow passwordless "log in as <role>" on the login page.
    demo_mode: bool = True
    demo_password: SecretStr | None = None

    # LLM providers: a provider is enabled only when its credential/URL is set.
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    ollama_base_url: str | None = None
    llm: LLMSettings = Field(default_factory=LLMSettings)

    # MCP tool server and agent loop.
    mcp_url: str = "http://localhost:8001/mcp"
    mcp_internal_secret: SecretStr = SecretStr(DEV_MCP_SECRET)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    agent: AgentSettings = Field(default_factory=AgentSettings)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _require_real_secrets_in_prod(self) -> "Settings":
        if self.app_env is AppEnv.PROD:
            secret = self.jwt_secret.get_secret_value()
            if secret == DEV_JWT_SECRET or len(secret) < MIN_PROD_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET must be set to a random string of at least "
                    f"{MIN_PROD_SECRET_LENGTH} characters in production"
                )
            mcp_secret = self.mcp_internal_secret.get_secret_value()
            if mcp_secret == DEV_MCP_SECRET or len(mcp_secret) < MIN_PROD_SECRET_LENGTH:
                raise ValueError(
                    f"MCP_INTERNAL_SECRET must be a random string of at least "
                    f"{MIN_PROD_SECRET_LENGTH} characters in production"
                )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_file = _config_dir() / f"{_resolve_app_env().value}.yaml"
        yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_file)
        return init_settings, env_settings, dotenv_settings, yaml_settings


@lru_cache
def get_settings() -> Settings:
    return Settings()
