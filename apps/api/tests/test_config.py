import pytest
from pydantic import SecretStr

from brand_api.config import AppEnv, Settings


def test_cors_origins_accepts_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.vercel.app, https://b.example.com")
    assert Settings().cors_origins == ["https://a.vercel.app", "https://b.example.com"]


def test_empty_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "")
    assert Settings().redis_url == "redis://localhost:6379/0"


def test_test_profile_yaml_is_loaded() -> None:
    settings = Settings()
    assert settings.app_env is AppEnv.TEST
    assert settings.log_level == "WARNING"


def test_switching_provider_is_one_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM__PROVIDER", "anthropic")
    llm = Settings().llm
    assert llm.provider == "anthropic"
    assert llm.providers["anthropic"].complex == "claude-opus-5-5"


def test_yaml_profile_values_merge_with_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM__TIMEOUT_S", "12")
    llm = Settings().llm
    assert llm.timeout_s == 12
    assert llm.provider == "groq"  # from config/test.yaml


@pytest.mark.parametrize("secret", [None, "too-short"])
def test_prod_refuses_weak_jwt_secret(secret: str | None) -> None:
    kwargs = {"jwt_secret": secret} if secret else {}
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(app_env=AppEnv.PROD, **kwargs)  # type: ignore[arg-type]


def test_prod_accepts_strong_jwt_secret() -> None:
    settings = Settings(
        app_env=AppEnv.PROD, jwt_secret=SecretStr("s" * 48), mcp_internal_secret=SecretStr("m" * 48)
    )
    assert settings.app_env is AppEnv.PROD


def test_pasted_secrets_are_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    # A trailing newline from a dashboard paste would make httpx reject the auth header.
    monkeypatch.setenv("GROQ_API_KEY", "gsk_key\n")
    monkeypatch.setenv("REDIS_URL", " rediss://default:pw@host:6379 ")
    settings = Settings()
    assert settings.groq_api_key == SecretStr("gsk_key")
    assert settings.redis_url == "rediss://default:pw@host:6379"
