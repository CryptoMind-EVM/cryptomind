"""Rate limiting must use shared storage in production."""

import pytest


def test_production_rejects_missing_redis(monkeypatch):
    from api.middleware.rate_limit import get_rate_limit_storage

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        get_rate_limit_storage()


def test_development_can_use_memory_rate_limit_storage(monkeypatch):
    from api.middleware.rate_limit import get_rate_limit_storage

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)

    assert get_rate_limit_storage() == ("memory://", "memory")
