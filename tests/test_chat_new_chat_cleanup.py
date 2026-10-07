"""開聊天頁的「New Chat」自動清理不能刪掉有內容的對話（2026-09-25 盤查）。

根因：chat-init.js 只看標題 'New Chat' 就刪；後端只在第一則「使用者」訊息
才把預設標題換掉（save_chat_message 的 upsert），所以有訊息但標題仍是
預設值的對話會連同歷史一起被刪。

修法：session 清單帶 has_messages，前端只清沒有訊息的空對話，而且不動
目前對話／上次開啟的對話。前端行為在 tests/js/chat_init_cleanup.mjs 實跑。
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import core.database.chat as chat_mod

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql: list[str] = []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchall(self):
        return self.rows


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


def test_get_sessions_reports_whether_session_has_messages(monkeypatch):
    ts = datetime(2026, 9, 25, 12, 0, 0)
    cur = _Cursor(
        [
            ("s-history", "New Chat", ts, ts, 0, True, None),
            ("s-empty", "New Chat", ts, ts, 0, False, None),
        ]
    )
    monkeypatch.setattr(chat_mod, "get_connection", lambda: _Conn(cur))

    rows = chat_mod.get_sessions(user_id="u1")

    assert [r["has_messages"] for r in rows] == [True, False], (
        "清單沒帶 has_messages，前端分不出空對話與有內容的對話"
    )
    sql = cur.sql[0]
    assert "EXISTS" in sql and "conversation_history" in sql, sql


def test_chat_init_only_cleans_empty_new_chats_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "chat_init_cleanup.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "chat_init_cleanup: ok" in proc.stderr
