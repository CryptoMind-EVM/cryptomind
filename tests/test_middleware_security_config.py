"""Production middleware configuration must fail closed."""

import pytest


def test_production_rejects_wildcard_cors(monkeypatch):
    from api.middleware_setup import get_security_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("ALLOWED_HOSTS", "app.example.com")

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        get_security_config()


def test_production_requires_allowed_hosts(monkeypatch):
    from api.middleware_setup import get_security_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)

    with pytest.raises(RuntimeError, match="ALLOWED_HOSTS"):
        get_security_config()


def test_production_uses_explicit_origins_and_hosts(monkeypatch):
    from api.middleware_setup import get_security_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("ALLOWED_HOSTS", "app.example.com,api.example.com")

    config = get_security_config()

    assert config["is_production"] is True
    assert config["origins"] == ["https://app.example.com"]
    assert config["allowed_hosts"] == ["app.example.com", "api.example.com"]


def test_cors_origins_strips_trailing_slash(monkeypatch):
    """Trailing slash on origin breaks CORS matching — browsers send
    Origin: https://app.example.com (no slash); an allowlist entry with a
    trailing slash won't match, so legit cross-origin requests get blocked.

    Configs like CORS_ORIGINS=https://cryptomind-ton.zeabur.app/ are a common
    mistake. get_security_config must normalize trailing slashes away.
    """
    from api.middleware_setup import get_security_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com/")
    monkeypatch.setenv("ALLOWED_HOSTS", "app.example.com")

    config = get_security_config()
    assert config["origins"] == ["https://app.example.com"], (
        "trailing slash must be stripped or CORS will silently reject legit origins"
    )


def test_cors_origins_strips_trailing_slash_multiple(monkeypatch):
    """Multiple origins, each possibly with a trailing slash, all normalized."""
    from api.middleware_setup import get_security_config

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CORS_ORIGINS", "https://a.com/, https://b.com")
    monkeypatch.setenv("ALLOWED_HOSTS", "a.com")

    config = get_security_config()
    assert config["origins"] == ["https://a.com", "https://b.com"]
