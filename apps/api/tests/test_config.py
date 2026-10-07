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
