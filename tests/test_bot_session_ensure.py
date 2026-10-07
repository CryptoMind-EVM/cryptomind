"""bot 聊天管線的 session 建立必須冪等（2026-09-09）。

LINE 上線第一位真人使用者即在第二則訊息踩爆：``run_bot_chat`` 對預設
session 每則訊息都呼叫裸 INSERT 的 ``create_session`` → 撞 ``sessions_pkey``
（UniqueViolation）→ 使用者收到「處理時發生問題」。Telegram 路徑此前
無人生產使用（生產庫 0 個 ``telegram:%`` session），同一顆雷埋了數個月。

修法：``ensure_session``——呼叫 ``create_session``，只把 UniqueViolation
（= session 已存在）視為成功，其他 DB 錯誤照樣傳播。
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg2.errors
import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def rollback(self):
        pass

    def close(self):
        pass


class TestEnsureSession:
    def test_duplicate_key_is_success_not_error(self, monkeypatch):
        """session 已存在（UniqueViolation）＝目的已達成，不得往上拋——
        bot 第二則訊息就會走到這個分支。"""
        from core.database import chat as chat_mod

        class _DupCursor:
            def execute(self, sql, params=None):
                raise psycopg2.errors.UniqueViolation

        monkeypatch.setattr(
            chat_mod, "get_connection", lambda: _FakeConn(_DupCursor())
        )

        chat_mod.ensure_session("bot:sid", "Bot Chat", "u1")

    def test_duplicate_key_does_not_log_error_noise(self, monkeypatch, caplog):
        """重複鍵是 ensure_session 的正常路徑——不得留 error 級日誌。

        2026-09-09 修復前：create_session 在 re-raise 前先 logger.error，
        於是每則 bot 訊息（session 已存在時）都炸一行「Session create
        error: duplicate key…」，把真正的錯誤淹沒在噪音裡。
        """
        import logging

        from core.database import chat as chat_mod

        class _DupCursor:
            def execute(self, sql, params=None):
                raise psycopg2.errors.UniqueViolation

        monkeypatch.setattr(
            chat_mod, "get_connection", lambda: _FakeConn(_DupCursor())
        )

        with caplog.at_level(logging.ERROR, logger="core.database.chat"):
            chat_mod.ensure_session("bot:sid", "Bot Chat", "u1")

        noise = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not noise, f"重複鍵不該留 error 日誌：{[r.getMessage() for r in noise]}"

    def test_other_db_errors_still_propagate(self, monkeypatch):
        """冪等只對重複鍵冪等；真實 DB 錯誤要 re-raise（對齊 create_session
        「失敗必須讓上層知道」的約定，endpoint 才回得了 503）。"""
        from core.database import chat as chat_mod

        class _BrokenCursor:
            def execute(self, sql, params=None):
                raise RuntimeError("simulated DB write failure")

        monkeypatch.setattr(
            chat_mod, "get_connection", lambda: _FakeConn(_BrokenCursor())
        )

        with pytest.raises(RuntimeError, match="simulated DB write failure"):
            chat_mod.ensure_session("bot:sid", "Bot Chat", "u1")

    def test_exported_from_database_package(self):
        from core.database import ensure_session  # noqa: F401


def test_run_bot_chat_uses_idempotent_session_ensure():
    """訊息路徑不得再呼叫裸 INSERT 的 create_session（源碼接線斷言）。"""
    src = (REPO / "api" / "routers" / "telegram_chat.py").read_text(encoding="utf-8")
    call = re.search(
        r"if session_id == default_session_id:\s*\n\s*await run_sync\((\w+),", src
    )
    assert call, "找不到 run_bot_chat 的 session 建立呼叫"
    assert call.group(1) == "ensure_session", (
        "bot 每則訊息都會走到這裡——裸 INSERT 的 create_session 在第二則"
        "訊息就撞 sessions_pkey（2026-09-09 LINE 生產事故）"
    )
