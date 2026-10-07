from core.redis_url import redact_redis_url, resolve_redis_url


def test_resolve_redis_url_prefers_explicit_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example-redis:6379/3")
    monkeypatch.setenv("REDIS_HOST", "ignored-host")

    url, source = resolve_redis_url()

    assert source == "REDIS_URL"
    assert url == "redis://example-redis:6379/3"


def test_resolve_redis_url_builds_from_host(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "service-abc")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("REDIS_DB", "0")

    url, source = resolve_redis_url()

    assert source == "REDIS_HOST"
    assert url == "redis://service-abc:6379/0"


def test_resolve_redis_url_empty_when_missing(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)

    url, source = resolve_redis_url()

    assert source == ""
    assert url == ""


def test_resolve_redis_url_builds_auth(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("REDIS_HOST", "service-auth")
    monkeypatch.setenv("REDIS_PASSWORD", "pass@word")
    monkeypatch.setenv("REDIS_DB", "1")

    url, source = resolve_redis_url()

    assert source == "REDIS_HOST"
    assert url == "redis://:pass%40word@service-auth:6379/1"


# ── redact_redis_url：log 用，絕不可漏出密碼 ──────────────────────────────────


def test_redact_hides_password_only_url():
    """redis://:secret@host 形式（Zeabur REDIS_URL 常見）密碼必須遮蔽。"""
    out = redact_redis_url("redis://:Sn0Xsecret@host:6379/0")
    assert "Sn0Xsecret" not in out
    assert out == "redis://***@host:6379/0"


def test_redact_hides_password_with_username():
    """user:secret 形式保留 user、遮密碼。"""
    out = redact_redis_url("redis://alice:hunter2@host:6379/0")
    assert "hunter2" not in out
    assert "alice" in out
    assert out == "redis://alice:***@host:6379/0"


def test_redact_passthrough_when_no_password():
    """無密碼的 URL 原樣輸出（安全）。"""
    out = redact_redis_url("redis://host:6379/0")
    assert out == "redis://host:6379/0"


def test_redact_never_leaks_real_secret():
    """無論輸入什麼，回傳值絕不能包含原始密碼片段。"""
    secret = "Sup3r$ecret!@#"
    out = redact_redis_url(f"redis://:{secret}@host:6379")
    assert secret not in out


# ── memory://：開發／測試明確不用 Redis ─────────────────────────────────────


def test_database_cache_treats_memory_url_as_no_redis(monkeypatch, caplog):
    """core.database.cache 以前不認 memory://，照樣拿去 from_url → ValueError，
    每次呼叫都重試並印一行 WARNING（shared_cache／analysis_queue 早就認得）。
    測試環境就是 memory://，那行 WARNING 擠掉了 RunMetrics 測試要驗的 log。"""
    import logging

    import redis

    from core.database import cache

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("memory:// 不該去連 Redis")

    monkeypatch.setenv("REDIS_URL", "memory://")
    monkeypatch.setattr(cache, "_redis_client", None)
    monkeypatch.setattr(cache, "_redis_available", False)
    monkeypatch.setattr(redis, "from_url", _must_not_connect)

    with caplog.at_level(logging.WARNING, logger="core.database.cache"):
        assert cache.get_redis_client() is None
        assert cache.get_redis_client() is None
    assert not caplog.records, caplog.messages
