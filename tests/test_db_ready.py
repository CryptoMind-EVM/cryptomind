"""Tests for the database ready-gate coordination (core/db_ready.py).

Covers both the original async event and the new sync gate added to let
`get_connection()` wait for lifespan's background init instead of racing it.
"""

import threading
import time

import pytest

import core.db_ready as db_ready


@pytest.fixture(autouse=True)
def _reset_state():
    """每個測試前重置 db_ready 模組狀態，避免互相污染。"""
    db_ready.reset_db_ready_state()
    yield
    db_ready.reset_db_ready_state()


# ---------------------------------------------------------------------------
# Sync gate — mark / wait / is_db_init_managed
# ---------------------------------------------------------------------------


class TestSyncGate:
    def test_is_db_init_managed_false_by_default(self):
        assert db_ready.is_db_init_managed() is False

    def test_mark_db_init_started_sets_managed_flag(self):
        db_ready.mark_db_init_started()
        assert db_ready.is_db_init_managed() is True

    def test_reset_clears_managed_flag(self):
        db_ready.mark_db_init_started()
        db_ready.reset_db_ready_state()
        assert db_ready.is_db_init_managed() is False

    def test_mark_db_ready_sets_sync_gate(self):
        db_ready.mark_db_ready()
        # sync gate 應立即 return（已 set）
        t0 = time.time()
        db_ready.wait_for_db_ready_sync(timeout=1.0)
        assert time.time() - t0 < 0.1

    def test_mark_db_failed_sets_sync_gate_and_stores_error(self):
        db_ready.mark_db_failed(RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="failed"):
            db_ready.wait_for_db_ready_sync(timeout=1.0)

    def test_reset_clears_error(self):
        db_ready.mark_db_failed(RuntimeError("boom"))
        db_ready.reset_db_ready_state()
        db_ready.mark_db_ready()
        # 不應再 raise
        db_ready.wait_for_db_ready_sync(timeout=1.0)


class TestWaitForDbReadySync:
    def test_returns_immediately_when_already_ready(self):
        db_ready.mark_db_ready()
        t0 = time.time()
        db_ready.wait_for_db_ready_sync(timeout=2.0)
        assert time.time() - t0 < 0.05

    def test_times_out_when_never_ready(self):
        with pytest.raises(RuntimeError, match="still in progress"):
            db_ready.wait_for_db_ready_sync(timeout=0.2, poll_interval=0.02)

    def test_unblocks_when_ready_set_from_another_thread(self):
        """背景執行緒在短延遲後 mark ready，wait 應在 ready 後返回。"""
        def _setter():
            time.sleep(0.1)
            db_ready.mark_db_ready()

        threading.Thread(target=_setter, daemon=True).start()
        t0 = time.time()
        db_ready.wait_for_db_ready_sync(timeout=2.0, poll_interval=0.01)
        elapsed = time.time() - t0
        # 下限 0.08:確實等到了 ready(非立即返回)。
        # 上限放寬到 3.0:xdist 平行跑時 CPU 被其他 worker 佔用,
        # 0.1s sleep 可能被排程延遲;逾時 2.0s 才是真正失敗門檻。
        assert 0.08 < elapsed < 3.0

    def test_raises_quickly_on_failure(self):
        """init 失敗時應立即 raise，不等到逾時。原始錯誤以 __cause__ 鏈結。"""
        def _failer():
            time.sleep(0.1)
            db_ready.mark_db_failed(RuntimeError("init exploded"))

        threading.Thread(target=_failer, daemon=True).start()
        t0 = time.time()
        with pytest.raises(RuntimeError, match="Database initialization failed") as exc_info:
            db_ready.wait_for_db_ready_sync(timeout=2.0, poll_interval=0.01)
        assert time.time() - t0 < 1.0
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "init exploded" in str(exc_info.value.__cause__)
