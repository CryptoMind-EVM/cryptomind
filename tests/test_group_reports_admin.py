"""後台的群組檢舉（Part E）：列表（風險高的在前）、快照裡每個發言人的名字、群名、處理。

群組快照裡會有好幾個人講話（私訊只有兩個人），所以 names 要涵蓋快照裡所有發言人。
真 PostgreSQL、交易最後 rollback。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）
REPO = Path(__file__).resolve().parents[1]


async def _group_with_messages(s, u, make):
    from core.orm.group_chat_repo import group_chat_repo
    from core.orm.group_messages_repo import group_messages_repo

    gid = (await group_chat_repo.create_group(u["a"], "投資閒聊", session=s))["group"][
        "id"
    ]
    for m in ("b", "c"):
        await make.friends(u["a"], u[m])
        inv = await group_chat_repo.create_invites(gid, u["a"], [u[m]], session=s)
        await group_chat_repo.accept_invite(
            inv["invited"][0]["invite_id"], u[m], session=s
        )
    send = group_messages_repo.send_message
    await send(gid, u["b"], "大家好", session=s)
    await send(gid, u["a"], "歡迎", session=s)
    scam = (await send(gid, u["c"], "保證獲利快加我", session=s))["message"]
    return gid, scam


async def test_list_includes_group_name_and_every_speaker(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.group_reports_repo import group_reports_repo

    s, u, make = gc_pg
    gid, scam = await _group_with_messages(s, u, make)
    rep = await group_messages_repo.report_message(
        scam["id"], u["b"], "scam", session=s
    )

    listed = await group_reports_repo.list_reports(
        status="pending", limit=100, session=s
    )
    (row,) = [r for r in listed["reports"] if r["id"] == rep["report_id"]]
    assert row["group_id"] == gid and row["group_name"] == "投資閒聊"
    assert row["reporter_user_id"] == u["b"] and row["reported_user_id"] == u["c"]
    speakers = {m["from_user_id"] for m in row["snapshot"]}
    assert speakers == {u["a"], u["b"], u["c"]}
    assert set(row["names"]) >= speakers, "快照裡每個發言人都要有名字（群組不只兩個人）"


async def test_resolve(gc_pg):
    from core.orm.group_messages_repo import group_messages_repo
    from core.orm.group_reports_repo import group_reports_repo

    s, u, make = gc_pg
    _, scam = await _group_with_messages(s, u, make)
    rep = await group_messages_repo.report_message(
        scam["id"], u["b"], "scam", session=s
    )
    done = await group_reports_repo.resolve_report(
        rep["report_id"], "resolved", "已處理", u["a"], session=s
    )
    assert done["success"] is True and done["report"]["status"] == "resolved"
    again = await group_reports_repo.resolve_report(
        rep["report_id"], "dismissed", None, u["a"], session=s
    )
    assert again == {"success": False, "error": "already_resolved"}
    missing = await group_reports_repo.resolve_report(
        999999999, "resolved", None, u["a"], session=s
    )
    assert missing["error"] == "report_not_found"


def test_admin_routes_require_admin_and_are_registered():
    src = (REPO / "api/routers/admin/group_reports.py").read_text(encoding="utf-8")
    assert (
        '@router.get("/group-reports")' in src
        and '@router.post("/group-reports/{report_id}/resolve")' in src
    )
    assert src.count("Depends(require_admin)") == 2
    assert "group_reports_router" in (REPO / "api/routers/admin/__init__.py").read_text(
        encoding="utf-8"
    )


def test_admin_ui_has_group_reports_tab():
    """拿掉註解再比對：以前這題被自己寫的註解（提到 /api/admin/group-reports）滿足了"""
    import re

    js = (REPO / "web/js/admin.js").read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    assert 'data-click-arg="group-reports"' in code, "要有群組檢舉分頁"
    assert "this.loadDmReports('group')" in code, "群組分頁要用 group 載入"
    assert "`/api/admin/${kind}-reports?status=pending" in code, "列表網址照 kind 換"
    assert "`/api/admin/${k}-reports/${Number(reportId)}/resolve`" in code, "處理網址照 kind 換"
    assert "r.names?.[m.from_user_id]" in code, "群組快照每一則標發言人"
