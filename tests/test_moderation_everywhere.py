"""內容檢查擴到留言、檢舉排序、私訊檢舉、防詐回報（2026-10-01，接 test_post_moderation.py）。

- 論壇留言／推噓帶的話：跟發文同一道檢查，被擋回 422 content_blocked、不存
- 檢舉：背景打風險分數（微調模型「該擋」的機率），後台照危險程度排；社群投票佇列不顯示
- 私訊：只在被檢舉時才打分數，只看被檢舉的人最近幾則
- 防詐回報：本來就在引用詐騙訊息，只用保護型規則（貼出助記詞、索取助記詞、叫人匯款）
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _verdict(status):
    return {"status": status, "flagged": False, "score": 0.99, "category": None,
            "reasons": ["harmful_model"] if status == "block" else [], "signals": []}


@pytest.fixture(autouse=True)
def _reset_limiter():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


# ── 論壇留言／推噓 ────────────────────────────────────────────────────────────


def _comments_client(verdict):
    import api.routers.forum.comments as comments

    app = FastAPI()
    app.state.limiter = comments.limiter
    app.include_router(comments.router)
    app.dependency_overrides[comments.get_current_user] = lambda: {"user_id": "u1", "username": "u1"}
    add = AsyncMock(return_value={"success": True, "comment_id": 9})
    patches = [
        patch.object(comments, "check_post", new=AsyncMock(return_value=verdict)),
        patch.object(comments.forum_repo, "get_post_by_id", new=AsyncMock(return_value={"id": 1, "is_hidden": False, "user_id": "u1", "title": "t"})),
        patch.object(comments.forum_repo, "add_comment", new=add),
        patch.object(comments, "run_sync", new=AsyncMock(return_value={"remaining": 5, "limit": 5})),
        patch("core.audit.audit_log", new=MagicMock()),
    ]
    return TestClient(app), patches, add


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/forum/posts/1/comments", {"type": "comment", "content": "加 LINE 穩賺"}),
        ("/api/forum/posts/1/push", {"content": "加 LINE 穩賺"}),
        ("/api/forum/posts/1/boo", {"content": "加 LINE 穩賺"}),
    ],
)
def test_blocked_comments_and_reactions_are_not_saved(path, body):
    client, patches, add = _comments_client(_verdict("block"))
    for p in patches:
        p.start()
    try:
        if path.endswith("/comments"):
            resp = client.post(path, json=body)
        else:
            resp = client.post(path, params={"content": body["content"]})
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 422 and resp.json()["detail"] == "content_blocked", resp.text
    add.assert_not_called()


def test_clean_comment_is_saved():
    client, patches, add = _comments_client(_verdict("pass"))
    for p in patches:
        p.start()
    try:
        resp = client.post("/api/forum/posts/1/comments", json={"type": "comment", "content": "同意你的看法"})
    finally:
        for p in patches:
            p.stop()
    assert resp.status_code == 200
    add.assert_called_once()


# ── 風險分數 ─────────────────────────────────────────────────────────────────


@pytest.fixture
def model(monkeypatch):
    from core.moderation import service

    state = MagicMock(score=0.5)

    async def fake_classify(text):
        return None if state.score is None else {"block": state.score}

    monkeypatch.setattr(service, "_classify", fake_classify)
    return state


async def test_risk_of(model):
    from core.moderation.service import risk_of

    model.score = 0.93
    assert await risk_of("老師帶單") == {"score": 0.93, "category": None}, "只有模型分數，沒有寫死的規則加權"
    model.score = None
    assert await risk_of("老師帶單") is None, "檢查服務不在就不寫，不是當成 0"
    assert await risk_of("   ") is None


def test_dm_report_text_only_counts_the_reported_person():
    from core.moderation.reports import DM_RECENT_MESSAGES, dm_report_text

    snapshot = [{"from_user_id": "bad" if i % 2 else "victim", "content": f"m{i}"} for i in range(20)]
    text = dm_report_text(snapshot, "bad")
    assert text.split("\n") == [f"m{i}" for i in range(1, 20, 2)][-DM_RECENT_MESSAGES:]
    assert "m18" not in text, "檢舉人自己的話不算進對方的風險"


async def test_score_content_report_writes_the_score(model):
    """論壇檢舉：抓被檢舉文章的標題＋內文打分數，寫回 content_reports"""
    from core.moderation.reports import score_content_report

    model.score = 0.88
    executed = []

    class FakeResult:
        def first(self):
            return ("VIP 帶單", "加 LINE")

    class FakeSession:
        async def execute(self, stmt):
            executed.append(stmt)
            return FakeResult()

    risk = await score_content_report(5, "post", 7, session=FakeSession())
    assert risk == {"score": 0.88, "category": None}
    update = executed[-1]
    params = update.compile().params
    assert params["risk_score"] == 0.88 and params["risk_category"] is None


async def test_scoring_failure_is_swallowed(model):
    from core.moderation.reports import score_dm_report

    class Boom:
        async def execute(self, stmt):
            raise RuntimeError("db gone")

    assert await score_dm_report(1, "請提供你的助記詞", session=Boom()) is None


# ── 私訊檢舉：真 PostgreSQL，後台照分數排 ────────────────────────────────────


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
    except Exception as e:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")
    trans = await conn.begin()
    cols = (await conn.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'dm_reports'"
    ))).scalars().all()
    if "risk_score" not in cols:
        await trans.rollback()
        await conn.close()
        await engine.dispose()
        pytest.skip("測試庫還沒有 c065 欄位")
    session = AsyncSession(bind=conn, expire_on_commit=False)
    s = uuid.uuid4().hex[:8]
    users = {k: f"t-risk-{k}-{s}" for k in ("a", "b", "c")}
    for uid in users.values():
        await session.execute(text("INSERT INTO users (user_id, username) VALUES (:u, :u)"), {"u": uid})
    try:
        yield session, users
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


async def test_admin_dm_reports_are_sorted_by_risk(pg_session, model):
    from core.moderation.reports import dm_report_text, score_dm_report
    from core.orm.dm_reports_repo import dm_reports_repo
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    ids = {}
    for sender, text in (("a", "週末要不要去爬山"), ("c", "請提供你的助記詞我幫你解凍")):
        msg = (await messages_repo.send_message(u[sender], u["b"], text, session=s))["message"]
        report = (await dm_reports_repo.report_message(msg["id"], u["b"], "scam", None, session=s))["report"]
        ids[sender] = report["id"]
        model.score = 0.97 if sender == "c" else 0.05
        await score_dm_report(report["id"], dm_report_text(report["snapshot"], report["reported_user_id"]), session=s)

    listed = (await dm_reports_repo.list_reports(status="pending", limit=100, session=s))["reports"]
    mine = [r for r in listed if r["id"] in ids.values()]
    assert [r["id"] for r in mine] == [ids["c"], ids["a"]], "後檢舉的詐騙（0.97）排在先檢舉的閒聊前面"
    assert mine[0]["risk_score"] == pytest.approx(0.97) and mine[0]["risk_category"] is None
    assert mine[1]["risk_score"] == pytest.approx(0.05)


def test_report_endpoints_score_in_the_background_and_admin_sorts_by_risk():
    msgs = (REPO / "api" / "routers" / "messages.py").read_text(encoding="utf-8")
    assert "spawn(" in msgs and "score_dm_report(" in msgs
    gov = (REPO / "api" / "routers" / "governance.py").read_text(encoding="utf-8")
    assert "spawn(score_content_report(" in gov
    admin = (REPO / "api" / "routers" / "admin" / "forum.py").read_text(encoding="utf-8")
    assert "ORDER BY cr.risk_score DESC NULLS LAST" in admin
    # 社群投票佇列不看分數（不影響投票的人）
    repo = (REPO / "core" / "orm" / "governance_repo.py").read_text(encoding="utf-8")
    pending = repo[repo.index("async def get_pending_reports") : repo.index("async def get_report_by_id")]
    assert "risk_score" not in pending
    js = (REPO / "web" / "js" / "admin.js").read_text(encoding="utf-8")
    assert js.count("${this._riskBadge(r)}") == 2


# ── 防詐回報：只用保護型規則 ─────────────────────────────────────────────────




def test_scam_tracker_still_allows_quoting_the_scam():
    """受害者引用詐騙訊息當證據：加倍返還、模型分數都不用"""
    from core.validators.content_filter import filter_sensitive_content

    result = filter_sensitive_content("他傳訊息說 send 1 ETH to this address and receive 2 ETH back，我轉了之後就被封鎖，損失 1 ETH")
    assert result["valid"] is True and result["codes"] == []


def test_scam_tracker_frontend_explains_the_rule():
    js = (REPO / "web" / "scam-tracker" / "js" / "scam-tracker.js").read_text(encoding="utf-8")
    block = js[js.index("case 'content_validation_failed'") :][:600]
    assert "detail.codes" in block and "forum.moderation.reason." in block
    for router in ("comments.py", "reports.py"):
        src = (REPO / "api" / "routers" / "scam_tracker" / router).read_text(encoding="utf-8")
        assert '"codes": content_check.get("codes", [])' in src
