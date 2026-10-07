"""好友邀請通知（2026-09-29）。

後端早就會建 friend_request 通知，但處理完（接受／拒絕／對方取消）那筆一直掛在
鈴鐺、照樣顯示「接受／拒絕」鈕，再按只會 400；「對方已接受」也用 friend_request
型別，同樣長出接受／拒絕鈕。這裡鎖住：
1. 邀請處理掉 → 那筆變已讀並推給收件人其他分頁（notifications_read）。
2. 對方接受 → 發 friend_accepted（沒有操作鈕的型別）給邀請人。
3. 重送邀請不疊出第二筆。
前端（鈴鐺裡直接按接受也要真的送出、文字走 i18n）在 tests/js/friend_notifications.mjs。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# repo：resolve_friend_request_notifications（真 PostgreSQL，交易內 rollback）
# ---------------------------------------------------------------------------


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
    users = {k: f"t-fr-{k}-{suffix}" for k in ("me", "a", "b")}
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


async def test_resolve_only_clears_that_senders_pending_request(pg_session):
    from core.orm.notifications_repo import notifications_repo as repo

    s, u = pg_session

    async def make(user, ntype, sender):
        return await repo.create_notification(
            user, ntype, "t", "b", data={"from_user_id": sender}, session=s
        )

    from_a = await make(u["me"], "friend_request", u["a"])
    from_b = await make(u["me"], "friend_request", u["b"])
    accepted = await make(u["me"], "friend_accepted", u["a"])

    cleared = await repo.resolve_friend_request_notifications(
        u["me"], u["a"], session=s
    )
    assert cleared == [from_a["id"]]

    by_id = {n["id"]: n for n in await repo.get_notifications(u["me"], session=s)}
    assert by_id[from_a["id"]]["is_read"] is True
    assert by_id[from_b["id"]]["is_read"] is False, "別人的邀請不動"
    assert by_id[accepted["id"]]["is_read"] is False, "只清待處理的邀請"


# ---------------------------------------------------------------------------
# router：處理邀請時清掉通知、接受改發 friend_accepted
# ---------------------------------------------------------------------------


@pytest.fixture
def friends_env(monkeypatch):
    from api.routers import friends as mod

    calls: list = []

    async def ok(*args, **kwargs):
        return {"success": True}

    async def resolve(user_id, from_user_id, session=None):
        calls.append(("resolve", user_id, from_user_id))
        return [f"n-{from_user_id}"]

    async def create(user_id, notification_type, title, body, data=None, session=None):
        calls.append(("create", user_id, notification_type, data))
        return {"id": "new", "type": notification_type, "data": data}

    async def push(user_id, notification):
        calls.append(("push", user_id, notification["type"]))

    async def push_read(user_id, ids):
        calls.append(("push_read", user_id, ids))

    async def get_by_id(user_id, session=None):
        return {"user_id": user_id}

    for name in (
        "send_friend_request",
        "accept_friend_request",
        "reject_friend_request",
        "cancel_friend_request",
    ):
        monkeypatch.setattr(mod.friends_repo, name, ok)
    monkeypatch.setattr(
        mod.notifications_repo,
        "resolve_friend_request_notifications",
        resolve,
        raising=False,
    )
    monkeypatch.setattr(mod.notifications_repo, "create_notification", create)
    monkeypatch.setattr(mod, "push_notification_to_user", push)
    monkeypatch.setattr(mod, "push_notifications_read", push_read, raising=False)
    monkeypatch.setattr(mod.user_repo, "get_by_id", get_by_id)
    me = {"user_id": "me", "username": "Me"}
    return mod, calls, me


def _call(endpoint, **kwargs):
    # 繞過 slowapi 限流 decorator，直接測端點本體
    return endpoint.__wrapped__(request=MagicMock(), session=None, **kwargs)


async def test_accept_clears_pending_and_notifies_requester_as_accepted(friends_env):
    mod, calls, me = friends_env
    req = mod.FriendActionRequest(target_user_id="alice")
    await _call(mod.accept_request, req=req, current_user=me)

    assert ("resolve", "me", "alice") in calls
    assert ("push_read", "me", ["n-alice"]) in calls, "我其他分頁的鈴鐺也要同步清掉"
    created = [c for c in calls if c[0] == "create"]
    assert created and created[0][1] == "alice"
    assert created[0][2] == "friend_accepted", (
        "『對方已接受』不能再用 friend_request（會長出接受／拒絕鈕）"
    )


async def test_reject_clears_pending(friends_env):
    mod, calls, me = friends_env
    await _call(
        mod.reject_request,
        req=mod.FriendActionRequest(target_user_id="alice"),
        current_user=me,
    )
    assert ("resolve", "me", "alice") in calls
    assert ("push_read", "me", ["n-alice"]) in calls


async def test_cancel_clears_the_targets_pending(friends_env):
    mod, calls, me = friends_env
    await _call(
        mod.cancel_request,
        req=mod.FriendActionRequest(target_user_id="bob"),
        current_user=me,
    )
    assert ("resolve", "bob", "me") in calls, "我取消了，對方鈴鐺裡那筆要收掉"
    assert ("push_read", "bob", ["n-me"]) in calls


async def test_resend_does_not_stack_a_second_notification(friends_env):
    mod, calls, me = friends_env
    await _call(
        mod.send_request,
        req=mod.FriendActionRequest(target_user_id="bob"),
        current_user=me,
    )
    kinds = [c[0] for c in calls]
    assert kinds.index("resolve") < kinds.index("create"), "先收掉舊的再發新的"
    created = [c for c in calls if c[0] == "create"][0]
    assert created[1:3] == ("bob", "friend_request")


# ---------------------------------------------------------------------------
# 前端（node 實跑）
# ---------------------------------------------------------------------------


def test_frontend_friend_notifications_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "friend_notifications.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "friend_notifications: ok" in proc.stderr
