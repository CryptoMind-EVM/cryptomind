"""core.database.chat.find_turn：分享用的「那一輪問答」配對（答案由伺服器取，不信前端傳的文字）。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Cur:
    def __init__(self, asc_rows):
        self._desc = list(reversed(asc_rows))  # 資料庫是 ORDER BY id DESC

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self._desc[: self.params[1]]


class _Conn:
    def __init__(self, rows):
        self.cur = _Cur(rows)

    def cursor(self):
        return self.cur

    def close(self):
        pass


def _find(monkeypatch, rows, question, session_id="s1"):
    from core.database import chat

    monkeypatch.setattr(chat, "get_connection", lambda: _Conn(rows))
    return chat.find_turn(session_id, question)


def test_pairs_the_question_with_the_assistant_reply_after_it(monkeypatch):
    rows = [
        ("user", "BTC 現在怎麼看"),
        ("assistant", "整理中"),
        ("user", "ETH 呢"),
        ("assistant", "也在整理"),
    ]
    assert _find(monkeypatch, rows, "BTC 現在怎麼看") == {
        "question": "BTC 現在怎麼看",
        "answer": "整理中",
    }
    assert _find(monkeypatch, rows, "ETH 呢")["answer"] == "也在整理"


def test_same_question_asked_twice_takes_the_latest(monkeypatch):
    rows = [
        ("user", "BTC?"),
        ("assistant", "舊答案"),
        ("user", "BTC?"),
        ("assistant", "新答案"),
    ]
    assert _find(monkeypatch, rows, "BTC?")["answer"] == "新答案"


def test_matching_is_whitespace_insensitive(monkeypatch):
    rows = [("user", "BTC\n  現在   怎麼看"), ("assistant", "答")]
    assert _find(monkeypatch, rows, " BTC 現在 怎麼看 ")["answer"] == "答"


def test_unanswered_question_returns_none_and_never_borrows_the_next_turns_answer(
    monkeypatch,
):
    rows = [("user", "第一題"), ("user", "第二題"), ("assistant", "第二題的答案")]
    assert _find(monkeypatch, rows, "第一題") is None
    assert _find(monkeypatch, rows, "第二題")["answer"] == "第二題的答案"


def test_empty_assistant_reply_is_skipped_to_the_next_non_empty_one(monkeypatch):
    rows = [("user", "q"), ("assistant", "  "), ("assistant", "真正的答案")]
    assert _find(monkeypatch, rows, "q")["answer"] == "真正的答案"


def test_missing_question_or_bad_session_returns_none_without_touching_the_db(
    monkeypatch,
):
    from core.database import chat

    def boom():
        raise AssertionError("不該連資料庫")

    monkeypatch.setattr(chat, "get_connection", boom)
    assert chat.find_turn("default", "q") is None
    assert chat.find_turn("", "q") is None
    assert chat.find_turn("s1", "   ") is None
