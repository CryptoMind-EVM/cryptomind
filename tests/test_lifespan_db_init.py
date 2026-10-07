"""Lifespan DB init 行為測試（2026-07-20 cold-start SIGABRT + 優化修復）。

驗證四件事：

1. **Alembic migration 必須走 ``run_in_executor``**——過去同步呼叫會阻塞
   event loop，gunicorn heartbeat 偵測不到 worker 活著 → SIGABRT。

2. **``LIFESPAN_SKIP_ALEMBIC`` env flag**：Zeabur 上 docker-entrypoint.sh 已同步
   跑過 alembic upgrade head，lifespan 再跑只是 no-op 但會佔 advisory_lock 時間。
   設 true 可省 5-30s cold-start。本機開發預設 false。

3. **失敗路徑必須呼叫 ``mark_db_failed``**——避免 request 永遠等 120s。

4. **成功路徑必須呼叫 ``mark_db_ready``**——否則 request 永遠等 120s timeout。

5. **每個 DB init 步驟都有耗時 log**——方便 Zeabur log 排查哪步最慢。
"""

from __future__ import annotations

import inspect
import re


def _read_lifespan_source() -> str:
    import api.lifespan as lifespan_mod

    return inspect.getsource(lifespan_mod)


def test_lifespan_alembic_uses_run_in_executor_when_not_skipped():
    """未設 LIFESPAN_SKIP_ALEMBIC 時，command.upgrade 必須包在 run_in_executor 內。"""
    src = _read_lifespan_source()
    m = re.search(r"command\.upgrade\([^)]+\)", src)
    assert m, "lifespan 必須保留 command.upgrade 呼叫（被 LIFESPAN_SKIP_ALEMBIC 包起來）"
    upgrade_call = m.group(0)

    idx = src.find(upgrade_call)
    before = src[max(0, idx - 300) : idx]
    assert "run_in_executor" in before, (
        f"command.upgrade 必須包在 run_in_executor 內（避免阻塞 event loop / SIGABRT）。"
        f"前的內容：{before[-120:]!r}"
    )


def test_lifespan_skip_alembic_flag_exists():
    """LIFESPAN_SKIP_ALEMBIC env flag 必須存在（Zeabur 跳過重複 Alembic）。"""
    src = _read_lifespan_source()
    assert "LIFESPAN_SKIP_ALEMBIC" in src, (
        "lifespan 必須檢查 LIFESPAN_SKIP_ALEMBIC env（Zeabur 已由 docker-entrypoint.sh 跑過）"
    )
    # 預設 false（本機開發維持原行為）
    assert re.search(
        r'LIFESPAN_SKIP_ALEMBIC["\']\s*,\s*["\']false["\']', src
    ), "LIFESPAN_SKIP_ALEMBIC 預設應為 'false'（不破壞本機開發）"


def test_lifespan_init_db_uses_run_in_executor():
    """init_db 也必須走 run_in_executor（regression guard）。"""
    src = _read_lifespan_source()
    assert re.search(r"run_in_executor\([^,]+,\s*init_db", src), (
        "init_db 必須透過 run_in_executor 執行"
    )


def test_lifespan_mark_db_failed_called_on_init_failure():
    """init_db 失敗時必須呼叫 mark_db_failed，否則 request 永遠等 120s。"""
    src = _read_lifespan_source()
    assert "mark_db_failed" in src, (
        "init_db 失敗路徑必須呼叫 mark_db_failed，否則 wait_for_db_ready_sync 永遠等到 timeout"
    )


def test_lifespan_mark_db_ready_called_on_success():
    """成功路徑必須呼叫 mark_db_ready，否則 request 永遠等 120s。"""
    src = _read_lifespan_source()
    assert "mark_db_ready()" in src, (
        "成功路徑必須呼叫 mark_db_ready()，否則 request 永遠等 120s timeout"
    )


def test_lifespan_db_init_step_timers_logged():
    """每個 DB init 步驟都要記錄耗時（Zeabur log 排查用）。

    預期看到三個步驟的耗時 log：
    - Database initialized (X.XXs)
    - ORM Alembic migration complete (head) (X.XXs)（若未 skip）
    - Tools catalog seeded (X.XXs)
    """
    src = _read_lifespan_source()
    # init_db 計時
    assert re.search(r"Database initialized.*%\.2fs", src), (
        "init_db 完成應記錄耗時（Database initialized %.2fs）"
    )
    # seed_tools_catalog 計時
    assert re.search(r"Tools catalog seeded.*%\.2fs", src), (
        "seed_tools_catalog 完成應記錄耗時（Tools catalog seeded %.2fs）"
    )


def test_mark_db_ready_called_before_seed_tools_catalog():
    """mark_db_ready() 必須在 seed_tools_catalog 之前呼叫（2026-07-20 修復）。

    Root cause：seed_tools_catalog 內部呼叫 get_connection()，而 get_connection
    在 server 環境會 wait_for_db_ready_sync() 等 ready。過去 mark_db_ready()
    放在 seed 之後，導致 seed 自己被自己的 init gate 卡死，log 噴
    "Database initialization is still in progress"。

    修復：把 mark_db_ready() 移到 init_db + alembic 完成、seed 之前。
    """
    src = _read_lifespan_source()
    mark_pos = src.find("mark_db_ready()")
    seed_pos = src.find("seed_tools_catalog")
    # 兩者都應該存在於 _init_database_background 內
    assert mark_pos != -1, "lifespan 必須呼叫 mark_db_ready()"
    assert seed_pos != -1, "lifespan 必須呼叫 seed_tools_catalog"
    assert mark_pos < seed_pos, (
        "mark_db_ready() 必須在 seed_tools_catalog 之前呼叫 — "
        "否則 seed 內的 get_connection() 會被自己的 init gate 卡住，"
        "log 噴 'Database initialization is still in progress'"
    )
