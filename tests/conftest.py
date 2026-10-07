"""
Pytest configuration and fixtures
"""

import os
from unittest.mock import AsyncMock

os.environ["DATABASE_URL"] = "postgresql://test:test@localhost:5432/test"
# 測試沒有真的 DB：連線池只試一次（預設重試 10×3 秒，Windows 上連 localhost 被拒還要再等 ~2 秒）
os.environ["DB_POOL_INIT_MAX_RETRIES"] = "1"
for _key in list(os.environ):
    if _key.startswith("POSTGRESQL_") or _key == "POSTGRES_DB":
        os.environ.pop(_key)
os.environ["REDIS_URL"] = "memory://"
# 論壇發文內容檢查：測試不打真的 moderation 容器——連線被拒＝unavailable（照常發文）；
# tests/test_post_moderation.py 自己換掉模型回應
os.environ["MODERATION_URL"] = "http://127.0.0.1:9"
for _key in list(os.environ):
    if _key.startswith("REDIS_") and _key != "REDIS_URL":
        os.environ.pop(_key)
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-for-testing-1234567890")
os.environ.setdefault("TEST_MODE", "true")
os.environ.setdefault("TEST_MODE_CONFIRMATION", "I_UNDERSTAND_THE_RISKS")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ.setdefault("ENVIRONMENT", "development")


import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    from api_server import app

    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def auth_headers():
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "test-user-001"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    return {"X-Admin-Key": os.getenv("ADMIN_API_KEY", "test-admin-key")}


@pytest.fixture
def mock_llm_client():
    return AsyncMock()


@pytest.fixture(autouse=True)
def _isolate_response_cache():
    """清空 agent 回覆快取，避免跨測試命中。

    根因：core/agents/response_cache.py 是模組級 dict（程序內 LRU）。key 是
    (user_id, normalized_query, language)，而多數測試共用同一組假 user 與 query，
    先跑的測試存進去之後，後面同 query 的測試會直接命中快取、agent 根本不會被
    呼叫（表現成 execute_streaming.await_count == 0）。
    """
    from core.agents import response_cache

    response_cache.clear()
    try:
        yield
    finally:
        response_cache.clear()


@pytest.fixture(autouse=True)
def _isolate_security_monitor(tmp_path, monkeypatch):
    """把 SecurityMonitor 的儲存路徑隔離到暫存目錄，避免測試汙染 repo。

    根因：core/security_monitor.py 的 get_security_monitor() 用模組級單例，
    預設寫死相對路徑 data/security_events.jsonl。某些測試（如達 auth 失敗閾值）
    會觸發 log_security_event → 真實寫檔。本 fixture 讓每次測試都用 tmp 路徑，
    並在結束後清除單例，確保跨測試不殘留。
    """
    import core.security_monitor as sm

    tmp_file = tmp_path / "security_events.jsonl"
    # 注入臨時單例，避免 get_security_monitor() 建立寫死路徑的預設單例
    sm._global_monitor = sm.SecurityMonitor(storage_path=str(tmp_file))
    try:
        yield
    finally:
        sm._global_monitor = None


# ── 測試庫的資料表要在第一個測試之前就建好 ─────────────────────────────────────
#
# gc_pg 這類 fixture 直接用 asyncpg 連資料庫，自己不會跑 init_db()；沒有別的測試先觸發
# get_connection()（第一次呼叫才會 init_db）就會 `relation "users" does not exist`。
# 以前純靠「碰巧先跑到別的測試」，xdist 分配不同時就變成時好時壞。
# 多個 worker 同時進來：用 advisory lock 排隊，避免兩邊同時 CREATE TABLE 撞出競態。
_SCHEMA_INIT_LOCK_KEY = 70_320_252


@pytest.fixture(scope="session", autouse=True)
def _test_db_schema_ready():
    lock_conn = None
    try:
        import psycopg2

        lock_conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=2)
        lock_conn.autocommit = True
        lock_conn.cursor().execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_INIT_LOCK_KEY,))
        from core.database import get_connection

        get_connection().close()  # 第一次呼叫會跑 init_db，把表建好
    except Exception:  # noqa: BLE001 — 連不到 DB：需要 DB 的測試自己會 skip
        pass
    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()  # 連線關閉＝session 層級的 advisory lock 自動釋放
            except Exception:  # noqa: BLE001
                pass
    yield
