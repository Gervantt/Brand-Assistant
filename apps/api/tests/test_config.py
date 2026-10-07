import pytest

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
