"""私訊檢舉（2026-09-29）。

收回會清空原文（c058），所以證據改在檢舉當下留：被檢舉那則＋前 10 則的文字存成快照。
快照由後端撈，不收前端傳的內容；只能檢舉對方傳的；同一則只能檢舉一次；可順便封鎖。
只有管理員看得到（不走論壇的社群投票，私訊不能給其他會員看）。
真 PostgreSQL，交易內跑完 rollback，連不到就 skip。
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
async def pg_session():
    from dotenv import load_dotenv
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from core.orm.session import _normalize_pg_url

    load_dotenv()
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("沒有 PostgreSQL DATABASE_URL")
    engine = create_async_engine(_normalize_pg_url(url))
    try:
        conn = await engine.connect()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    users = {k: f"t-rep-{k}-{suffix}" for k in ("a", "b", "x")}
    for uid in users.values():
        await session.execute(
            text("INSERT INTO users (user_id, username) VALUES (:u, :u)"), {"u": uid}
        )
    try:
        yield session, users
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def _chat(s, u, n):
    """a、b 輪流傳 n 則，回傳訊息（舊到新）"""
    from core.orm.messages_repo import messages_repo

    out = []
    for i in range(n):
        frm, to = (u["a"], u["b"]) if i % 2 == 0 else (u["b"], u["a"])
        out.append(
            (await messages_repo.send_message(frm, to, f"m{i}", session=s))["message"]
        )
    return out


async def test_report_snapshots_message_and_ten_before(pg_session):
    from core.orm.dm_reports_repo import dm_reports_repo

    s, u = pg_session
    msgs = await _chat(s, u, 14)
    target = msgs[12]  # a 傳的（偶數）→ b 檢舉

    result = await dm_reports_repo.report_message(
        target["id"], u["b"], "scam", "他叫我匯款", session=s
    )

    assert result["success"] is True
    snap = result["report"]["snapshot"]
    assert [m["content"] for m in snap] == [f"m{i}" for i in range(2, 13)], (
        "該則＋前 10 則，舊到新"
    )
    assert snap[-1]["id"] == target["id"]
    assert set(snap[0]) == {
        "id",
        "from_user_id",
        "content",
        "message_type",
        "created_at",
    }
    assert result["report"]["reported_user_id"] == u["a"]
    assert result["report"]["status"] == "pending"


async def test_report_rules(pg_session):
    from core.orm.dm_reports_repo import dm_reports_repo

    s, u = pg_session
    mine, theirs = await _chat(s, u, 2)  # mine=a 傳的、theirs=b 傳的

    async def report(mid, reporter, **kw):
        return await dm_reports_repo.report_message(
            mid, reporter, "spam", None, session=s, **kw
        )

    assert (await report(mine["id"], u["a"]))["error"] == "cannot_report_own"
    assert (await report(mine["id"], u["x"]))["error"] == "message_not_found"
    assert (await report(10**9, u["b"]))["error"] == "message_not_found"
    assert (await report(theirs["id"], u["a"]))["success"] is True
    assert (await report(theirs["id"], u["a"]))["error"] == "already_reported"


async def test_evidence_survives_recall_and_block_option(pg_session):
    from sqlalchemy import text

    from core.orm.dm_reports_repo import dm_reports_repo
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    scam = (
        await messages_repo.send_message(u["a"], u["b"], "私鑰給我幫你解凍", session=s)
    )["message"]

    result = await dm_reports_repo.report_message(
        scam["id"], u["b"], "scam", None, block=True, session=s
    )
    await messages_repo.recall_message(scam["id"], u["a"], session=s)

    stored = await dm_reports_repo.list_reports(status="pending", session=s)
    mine = next(r for r in stored["reports"] if r["id"] == result["report"]["id"])
    assert mine["snapshot"][-1]["content"] == "私鑰給我幫你解凍", "收回後證據還在快照裡"
    assert result["blocked"] is True
    status = (
        await s.execute(
            text(
                "SELECT status FROM friendships WHERE user_id = :b AND friend_id = :a"
            ),
            {"a": u["a"], "b": u["b"]},
        )
    ).scalar_one()
    assert status == "blocked"


async def test_resolve_report(pg_session):
    from core.orm.dm_reports_repo import dm_reports_repo

    s, u = pg_session
    _, theirs = await _chat(s, u, 2)
    rid = (
        await dm_reports_repo.report_message(
            theirs["id"], u["a"], "other", "x", session=s
        )
    )["report"]["id"]

    done = await dm_reports_repo.resolve_report(
        rid, "resolved", "已停權", "admin-1", session=s
    )

    assert done["success"] is True
    pending = await dm_reports_repo.list_reports(status="pending", session=s)
    assert all(r["id"] != rid for r in pending["reports"]), "處理過的不在待處理清單"
    resolved = await dm_reports_repo.list_reports(status="resolved", session=s)
    row = next(r for r in resolved["reports"] if r["id"] == rid)
    assert row["admin_note"] == "已停權" and row["resolved_by"] == "admin-1"
    again = await dm_reports_repo.resolve_report(
        rid, "dismissed", None, "admin-1", session=s
    )
    assert again["error"] == "already_resolved"


def test_report_request_validation():
    from pydantic import ValidationError

    from api.routers.messages import ReportRequest

    assert ReportRequest(reason="scam").block is False
    with pytest.raises(ValidationError):
        ReportRequest(reason="because")
    with pytest.raises(ValidationError):
        ReportRequest(reason="other", note="x" * 501)


def test_admin_dm_report_routes_require_admin():
    from api.deps import require_admin
    from api.routers.admin.dm_reports import router

    paths = {route.path for route in router.routes}
    assert {"/dm-reports", "/dm-reports/{report_id}/resolve"} <= paths
    for route in router.routes:
        calls = {d.call for d in route.dependant.dependencies}
        assert require_admin in calls, f"{route.path} 沒有 require_admin"


# review 2026-09-29：勾了「同時封鎖」但封鎖失敗，檢舉（證據）不能跟著回滾
async def test_report_is_kept_when_block_fails(pg_session, monkeypatch):
    from core.orm import dm_reports_repo as mod
    from core.orm.dm_reports_repo import dm_reports_repo

    s, u = pg_session
    _, theirs = await _chat(s, u, 2)

    async def boom(*_a, **_kw):
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(mod.friends_repo, "block_user", boom)
    result = await dm_reports_repo.report_message(
        theirs["id"], u["a"], "harassment", None, block=True, session=s
    )

    assert result["success"] is True
    assert result["blocked"] is False
    stored = await dm_reports_repo.list_reports(status="pending", session=s)
    assert any(r["id"] == result["report"]["id"] for r in stored["reports"])


async def test_report_endpoint_never_returns_snapshot(monkeypatch):
    """快照只給管理員：使用者端的回應只有成功與否、有沒有封鎖"""
    from starlette.requests import Request

    import api.routers.messages as router

    async def fake_report(message_id, reporter, reason, note, block=False):
        return {
            "success": True,
            "blocked": block,
            "report": {"id": 1, "snapshot": [{"content": "secret"}]},
        }

    monkeypatch.setattr(router.dm_reports_repo, "report_message", fake_report)
    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    body = await router.report_message_endpoint.__wrapped__(
        req, 5, router.ReportRequest(reason="scam", block=True), {"user_id": "a"}
    )
    assert body == {"success": True, "blocked": True}
