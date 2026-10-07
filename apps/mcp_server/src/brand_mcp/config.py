from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_MCP_SECRET = "dev-only-insecure-mcp-secret-change-me"  # noqa: S105 - dev default


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_ignore_empty=True, extra="ignore", frozen=True
    )

    app_env: str = "local"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql://brand:brand@localhost:5433/brand"
    redis_url: str = "redis://localhost:6379/0"
    mcp_internal_secret: SecretStr = SecretStr(DEV_MCP_SECRET)
    mcp_host: str = "0.0.0.0"  # noqa: S104 - container networking
    mcp_port: int = 8001

    draft_ttl_s: int = Field(default=24 * 3600, gt=0)
    generation_max_tokens: int = Field(default=4096, gt=0)
    brandbook_min_confidence: float = Field(default=0.35, ge=0, le=1)


@lru_cache
def get_settings() -> McpSettings:
    return McpSettings()
