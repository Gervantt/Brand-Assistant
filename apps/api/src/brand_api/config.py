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
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from brand_api.llm.config import LLMSettings

REPO_ROOT = Path(__file__).resolve().parents[4]


class AppEnv(StrEnum):
    LOCAL = "local"
    PROD = "prod"
    TEST = "test"


def _resolve_app_env() -> AppEnv:
    raw = os.environ.get("APP_ENV") or dotenv_values(".env").get("APP_ENV") or AppEnv.LOCAL
    return AppEnv(raw)


def _config_dir() -> Path:
    return Path(os.environ.get("CONFIG_DIR", REPO_ROOT / "config"))


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

    jwt_secret: SecretStr = SecretStr("change-me-in-env")

    # LLM providers: a provider is enabled only when its credential/URL is set.
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    ollama_base_url: str | None = None
    llm: LLMSettings = Field(default_factory=LLMSettings)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

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
