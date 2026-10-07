from brand_api.db import normalize_database_url


def test_plain_local_url_gets_asyncpg_driver() -> None:
    cfg = normalize_database_url("postgresql://brand:brand@localhost:5432/brand")
    assert cfg.url.drivername == "postgresql+asyncpg"
    assert cfg.connect_args == {}


def test_heroku_style_scheme_is_accepted() -> None:
    cfg = normalize_database_url("postgres://u:p@db:5432/x")
    assert cfg.url.drivername == "postgresql+asyncpg"


def test_neon_sslmode_and_channel_binding_are_translated() -> None:
    cfg = normalize_database_url(
        "postgresql://u:p@ep-cool-1.eu-central-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert cfg.connect_args == {"ssl": "require"}
    assert dict(cfg.url.query) == {}
    assert "sslmode" not in cfg.url_string


def test_neon_pooler_disables_prepared_statement_caches() -> None:
    cfg = normalize_database_url(
        "postgresql://u:p@ep-cool-1-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require"
    )
    assert cfg.connect_args["statement_cache_size"] == 0
    assert cfg.url.query["prepared_statement_cache_size"] == "0"


def test_password_survives_rendering() -> None:
    cfg = normalize_database_url("postgresql://u:s3cr%40t@h/db")
    assert cfg.url.password == "s3cr@t"
    assert "s3cr%40t" in cfg.url_string
