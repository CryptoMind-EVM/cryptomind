"""Health endpoint cold-start 行為測試。

驗證關鍵假設：

1. **Cold-start 視窗**（lifespan DB init 進行中）：``/health`` 必須**立刻回 200**，
   不可以呼叫 ``get_connection()``（否則會卡在 ``wait_for_db_ready_sync(120s)``，
   導致 Zeabur healthcheck 自己逾時 → 容器被殺 → 無限重啟）。

2. **Init 失敗**：``/health`` 應回 503 + ``db_init_failed``，讓平台決定重啟。

3. **Init 完成**：``/health`` 才走原本的 SELECT 1 真實檢查。

4. **非 server 環境**（無 lifespan）：``is_db_init_managed() == False``，
   ``/health`` 走原 ``_check_db`` 路徑。
"""

from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse


@pytest.fixture
def reset_db_ready_state():
    """每個測試自動 reset，避免跨測試污染。"""
    from core.db_ready import reset_db_ready_state as _reset

    _reset()
    yield
    _reset()


def test_health_cold_start_returns_200_initializing(reset_db_ready_state):
    """Cold-start：init 進行中 → 200 + status=initializing，不打 DB。

    這是本次修復的核心：避免 healthcheck 在 cold-start 視窗卡死。
    """
    from api.health import health_check
    from core.db_ready import mark_db_init_started

    # 模擬 lifespan 剛啟動、DB init 進行中
    mark_db_init_started()
    # 不呼叫 mark_db_ready / mark_db_failed → is_db_ready() 仍 False

    response = asyncio_run(health_check())

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "initializing" in body
    assert "warming_up" in body


def test_health_init_failed_returns_503(reset_db_ready_state):
    """Init 失敗 → 503 + db_init_failed，讓平台知道要重啟。"""
    from api.health import health_check
    from core.db_ready import mark_db_failed, mark_db_init_started

    mark_db_init_started()
    mark_db_failed(RuntimeError("Alembic upgrade failed: relation exists"))

    response = asyncio_run(health_check())

    assert response.status_code == 503
    body = response.body.decode("utf-8")
    assert "db_init_failed" in body


def test_health_ready_runs_real_db_check(reset_db_ready_state, monkeypatch):
    """Init 完成 → 走原本 SELECT 1 真實檢查。

    確保 ready flag 不是只有「秒回 200」一條路——init 真的完成後，
    還是要確認 pool 是活著的（pool 可能因為 idle timeout 斷光）。
    """
    from api import health as health_mod
    from core.db_ready import mark_db_init_started, mark_db_ready

    mark_db_init_started()
    mark_db_ready()

    # Mock _check_db 驗證真的被呼叫到
    call_count = {"n": 0}

    def fake_check_db(get_conn, retries=2):
        call_count["n"] += 1
        return None  # SELECT 1 成功

    monkeypatch.setattr(health_mod, "_check_db", fake_check_db)

    response = asyncio_run(health_mod.health_check())

    assert response.status_code == 200
    body = response.body.decode("utf-8")
    assert "healthy" in body
    assert call_count["n"] == 1, "init 完成後應走真實 SELECT 1 檢查"


def test_health_non_server_env_runs_real_db_check(reset_db_ready_state, monkeypatch):
    """非 server 環境（is_db_init_managed=False）→ 直接走 _check_db。

    腳本/測試沒有 lifespan，沒有 managed init 可以等；必須自己打 DB。
    """
    from api import health as health_mod

    # 不呼叫 mark_db_init_started → is_db_init_managed() 仍 False

    call_count = {"n": 0}

    def fake_check_db(get_conn, retries=2):
        call_count["n"] += 1
        return None

    monkeypatch.setattr(health_mod, "_check_db", fake_check_db)

    response = asyncio_run(health_mod.health_check())

    assert response.status_code == 200
    assert call_count["n"] == 1


def test_get_db_ready_error_accessor_exists():
    """新增的 public accessor：get_db_ready_error() 回 None 或 Exception。"""
    from core.db_ready import get_db_ready_error, mark_db_failed, reset_db_ready_state

    reset_db_ready_state()
    assert get_db_ready_error() is None

    mark_db_failed(ValueError("boom"))
    assert isinstance(get_db_ready_error(), ValueError)


def asyncio_run(coro):
    """跑 async 函數並回結果。"""
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)
