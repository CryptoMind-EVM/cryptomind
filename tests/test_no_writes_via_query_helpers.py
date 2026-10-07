"""守衛：INSERT／UPDATE／DELETE 不可經 ``DatabaseBase.query_one``／``query_all`` 送出。

那兩個 helper 的 context manager 只 close 不 commit，寫入會在連線關閉時 rollback——
表象是回傳 RETURNING 的列、資料其實不存在。已經中過兩次：trade_journal 寫入（2026-08-22）、
行事曆 ``store.add_event``（2026-09-12，系統事件與自訂提醒從 Phase B 上線起都沒存進去）。
寫入要走 ``DatabaseBase.execute``（會 commit）或 ``transaction()``。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
_CALL = re.compile(r'query_(?:one|all)\(\s*(?:f?"""|f?")(.*?)(?:"""|")', re.S)
_WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE)\b")


def _offenders():
    out = []
    for path in sorted((REPO / "core").rglob("*.py")) + sorted(
        (REPO / "api").rglob("*.py")
    ):
        src = path.read_text(encoding="utf-8")
        for m in _CALL.finditer(src):
            if _WRITE.search(m.group(1)):
                line = src[: m.start()].count("\n") + 1
                out.append(f"{path.relative_to(REPO)}:{line}")
    return out


def test_no_write_statements_through_query_helpers():
    assert _offenders() == [], "寫入語句走了不 commit 的 query helper（見檔頭說明）"


def test_calendar_add_event_commits():
    src = (REPO / "core" / "daily_brief" / "store.py").read_text(encoding="utf-8")
    body = src[src.index("def add_event(") : src.index("def delete_event(")]
    assert "with transaction() as conn:" in body and "RETURNING" in body
    assert (
        "DatabaseBase.query_one(" not in body
    )  # 註解裡提到 query_one 沒關係，呼叫才算
