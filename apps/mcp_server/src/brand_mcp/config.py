import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_MCP_SECRET = "dev-only-insecure-mcp-secret-change-me"  # noqa: S105 - dev default


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("BRAND_ENV_FILE", ".env") or None,
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
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

    # RAG
    embedding_provider: Literal["fastembed", "gemini"] = "fastembed"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    gemini_api_key: SecretStr | None = None
    gemini_embedding_model: str = "gemini-embedding-001"
    model_cache_dir: str | None = None  # fastembed model cache (baked into the prod image)
    reranker_enabled: bool = False
    reranker_model: str = "jinaai/jina-reranker-v2-base-multilingual"
    rag_candidates: int = Field(default=20, ge=5, le=100)
    # A hit counts as "found" above these scores (cosine similarity / reranker probability).
    # Cosine scales differ per embedding model; None = calibrated default for the provider
    # (decisions 019 and 032).
    min_similarity: float | None = Field(default=None, ge=0, le=1)
    min_rerank_score: float = Field(default=0.3, ge=0, le=1)
    chunk_target_chars: int = Field(default=1200, ge=200)
    chunk_overlap_chars: int = Field(default=200, ge=0)
    max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    seed_documents_dir: str | None = None  # ingest data/brands/<client-slug>/*.md at startup

    @property
    def similarity_threshold(self) -> float:
        if self.min_similarity is not None:
            return self.min_similarity
        return CALIBRATED_MIN_SIMILARITY[self.embedding_provider]


CALIBRATED_MIN_SIMILARITY = {"fastembed": 0.40, "gemini": 0.66}


@lru_cache
def get_settings() -> McpSettings:
    return McpSettings()
