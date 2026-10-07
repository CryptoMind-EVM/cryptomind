"""私訊即時體驗的後端契約（2026-09-29，參考 Teams／LINE 的做法）。

1. 同一個對話的未讀訊息通知合併成一筆（最新內容＋則數），不要一則訊息一列。
2. 讀了對話，該對話的訊息通知一起變已讀（多裝置、多分頁同步清掉鈴鐺）。
3. 「輸入中」是短暫事件：只轉給同對話、仍可互傳訊息的另一方，不存 DB；
   同一連線 1 秒內重複的丟掉，非成員／被封鎖／非好友一律不轉。

1、2 對 JSONB 查詢，只能用真的 PostgreSQL 驗（本機 DATABASE_URL，交易內跑完
rollback，連不到就 skip）；3 用 mock。前端（斷線無限重連、重連補抓、心跳、輸入中
節流、通知寬限期補抓、合併通知取代）在 tests/js/dm_realtime.mjs 實跑。
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def test_frontend_realtime_contract_node_gate():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "dm_realtime.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert "dm_realtime: ok" in proc.stderr


# ---------------------------------------------------------------------------
# 1、2：通知合併與已讀同步（真 PostgreSQL）
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
    users = {"me": f"t-dm-me-{suffix}", "a": f"t-dm-a-{suffix}"}
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


async def test_unread_message_notifications_collapse_per_conversation(pg_session):
    from core.orm.notifications_repo import notifications_repo as repo

    s, u = pg_session
    first = await repo.notify_new_message(
        u["me"], u["a"], "Alice", "早安", "7", session=s
    )
    second = await repo.notify_new_message(
        u["me"], u["a"], "Alice", "今天看盤嗎", "7", session=s
    )
    other = await repo.notify_new_message(
        u["me"], u["a"], "Alice", "另一個對話", "8", session=s
    )

    assert second["id"] == first["id"], "同對話的未讀通知要合併成同一筆"
    assert second["data"]["count"] == 2
    assert "今天看盤嗎" in second["body"], "合併後顯示最新一則內容"
    assert second["is_read"] is False
    assert other["id"] != first["id"], "不同對話各自一筆"

    rows = await repo.get_notifications(u["me"], session=s)
    assert len(rows) == 2


async def test_reading_conversation_clears_only_its_message_notifications(pg_session):
    from core.orm.notifications_repo import notifications_repo as repo

    s, u = pg_session
    n7 = await repo.notify_new_message(u["me"], u["a"], "Alice", "hi", "7", session=s)
    n8 = await repo.notify_new_message(u["me"], u["a"], "Alice", "yo", "8", session=s)

    cleared = await repo.mark_message_notifications_read(u["me"], 7, session=s)
    assert cleared == [n7["id"]]

    by_id = {n["id"]: n for n in await repo.get_notifications(u["me"], session=s)}
    assert by_id[n7["id"]]["is_read"] is True
    assert by_id[n8["id"]]["is_read"] is False

    # 讀過之後再來新訊息：開新的一筆，不要把已讀那筆翻回未讀
    again = await repo.notify_new_message(
        u["me"], u["a"], "Alice", "還在嗎", "7", session=s
    )
    assert again["id"] != n7["id"]
    assert again["data"]["count"] == 1

    assert await repo.mark_message_notifications_read(u["a"], 7, session=s) == [], (
        "只清自己的通知"
    )


# ---------------------------------------------------------------------------
# 3：「輸入中」轉送（mock）
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def typing_env(monkeypatch):
    from api.routers import messages as mod

    convs = {7: ("me", "friend"), 9: ("me", "blocker")}
    lookups = []

    async def fake_get_conversation_by_id(conversation_id, user_id, session=None):
        lookups.append(conversation_id)
        pair = convs.get(conversation_id)
        if not pair or user_id not in pair:
            return None
        return {"id": conversation_id, "user1_id": pair[0], "user2_id": pair[1]}

    async def fake_validate(sender, receiver, session=None):
        if receiver == "blocker":
            return {"valid": False, "error": "blocked"}
        return {"valid": True, "error": None}

    sent = []

    async def fake_send_to_user(user_id, data):
        sent.append((user_id, data))

    monkeypatch.setattr(
        mod.messages_repo, "get_conversation_by_id", fake_get_conversation_by_id
    )
    monkeypatch.setattr(mod.messages_repo, "validate_message_send", fake_validate)
    monkeypatch.setattr(mod.message_manager, "send_to_user", fake_send_to_user)
    clock = _Clock()
    return mod, sent, lookups, clock


async def test_typing_is_relayed_to_the_other_participant_only(typing_env):
    mod, sent, _, clock = typing_env
    state: dict = {}
    await mod._relay_typing("me", "Me", {"conversation_id": 7}, state, now=clock)
    assert sent == [
        (
            "friend",
            {
                "type": "typing",
                "conversation_id": 7,
                "from_user_id": "me",
                "from_username": "Me",
                "state": "start",
            },
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"conversation_id": 404},  # 不是成員／不存在
        {"conversation_id": 9},  # 對方封鎖
        {"conversation_id": "7"},  # 型別不對
        {"conversation_id": True},
        {},
    ],
)
async def test_typing_is_dropped_when_not_allowed(typing_env, payload):
    mod, sent, _, clock = typing_env
    await mod._relay_typing("me", "Me", payload, {}, now=clock)
    assert sent == []


async def test_typing_start_is_throttled_but_stop_goes_through(typing_env):
    mod, sent, lookups, clock = typing_env
    state: dict = {}
    await mod._relay_typing("me", "Me", {"conversation_id": 7}, state, now=clock)
    clock.t += 0.5
    await mod._relay_typing("me", "Me", {"conversation_id": 7}, state, now=clock)
    assert len(sent) == 1, "1 秒內重複的 start 丟掉"

    await mod._relay_typing(
        "me", "Me", {"conversation_id": 7, "state": "stop"}, state, now=clock
    )
    assert sent[-1][1]["state"] == "stop", "stop 不節流（送出／清空要立刻消失）"

    clock.t += 1.5
    await mod._relay_typing("me", "Me", {"conversation_id": 7}, state, now=clock)
    assert len(sent) == 3
    assert lookups == [7], "權限查詢要快取，不要每 3 秒打一次 DB"


async def test_stop_right_after_start_is_relayed(typing_env):
    """打一個字馬上送出：stop 不能被 0.2 秒的連線節流吞掉，否則對方的提示卡 6 秒。"""
    mod, sent, _, clock = typing_env
    state: dict = {}
    await mod._relay_typing("me", "Me", {"conversation_id": 7}, state, now=clock)
    clock.t += 0.05
    await mod._relay_typing(
        "me", "Me", {"conversation_id": 7, "state": "stop"}, state, now=clock
    )
    assert [d["state"] for _, d in sent] == ["start", "stop"]


async def test_stop_without_prior_start_is_dropped_without_db(typing_env):
    mod, sent, lookups, clock = typing_env
    await mod._relay_typing(
        "me", "Me", {"conversation_id": 7, "state": "stop"}, {}, now=clock
    )
    assert sent == []
    assert lookups == [], "stop 只轉給已驗證過的對象，不該為它查 DB"


async def test_permission_cache_evicts_oldest_not_everything(typing_env, monkeypatch):
    mod, sent, lookups, clock = typing_env
    monkeypatch.setattr(mod, "_TYPING_MAX_TARGETS", 2)
    state: dict = {}
    for conv in (7, 404, 405):  # 第三個進來時淘汰最舊的 7
        clock.t += 1.1
        await mod._relay_typing("me", "Me", {"conversation_id": conv}, state, now=clock)
    assert list(state["targets"]) == [404, 405]
    clock.t += 1.1
    await mod._relay_typing("me", "Me", {"conversation_id": 405}, state, now=clock)
    assert lookups == [7, 404, 405], "還在快取裡的不重查"


async def test_concurrent_first_notifications_still_collapse():
    """兩則幾乎同時到、那個對話還沒有未讀通知：只能留一筆（advisory lock 排隊）。"""
    import asyncio

    from dotenv import load_dotenv
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from core.orm.notifications_repo import notifications_repo as repo
    from core.orm.session import _normalize_pg_url

    load_dotenv()
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        pytest.skip("沒有 PostgreSQL DATABASE_URL")
    engine = create_async_engine(_normalize_pg_url(url))
    suffix = uuid.uuid4().hex[:8]
    me, a = f"t-dm-me-{suffix}", f"t-dm-a-{suffix}"
    try:
        async with engine.begin() as conn:
            for uid in (me, a):
                await conn.execute(
                    text("INSERT INTO users (user_id, username) VALUES (:u, :u)"),
                    {"u": uid},
                )
    except Exception as e:  # noqa: BLE001 — 連不到就 skip
        await engine.dispose()
        pytest.skip(f"PostgreSQL 連不到：{e}")

    async def notify(content):
        async with AsyncSession(engine, expire_on_commit=False) as s:
            async with s.begin():
                return await repo.notify_new_message(
                    me, a, "Alice", content, "77", session=s
                )

    try:
        # A 插了還沒 commit 時 B 進來：沒排隊的話 B 看不到 A 那筆，會自己再插一筆
        async with AsyncSession(engine, expire_on_commit=False) as sa:
            async with sa.begin():
                await repo.notify_new_message(me, a, "Alice", "一", "77", session=sa)
                task_b = asyncio.create_task(notify("二"))
                await asyncio.sleep(0.5)
        await asyncio.wait_for(task_b, 10)
        async with AsyncSession(engine) as s:
            rows = await repo.get_notifications(me, session=s)
        assert len(rows) == 1, f"並發也只能一筆，實際 {len(rows)} 筆"
        assert rows[0]["data"]["count"] == 2
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM notifications WHERE user_id = :u"), {"u": me}
            )
            await conn.execute(
                text("DELETE FROM users WHERE user_id IN (:a, :b)"), {"a": me, "b": a}
            )
        await engine.dispose()


async def test_send_response_carries_todays_quota(monkeypatch):
    """送出後直接帶回今日額度：前端輸入列下的「今日還能傳 N 則」不必每則再打一次 API。"""
    from unittest.mock import MagicMock

    from api.routers import messages as mod

    async def valid(*a, **k):
        return {"valid": True, "error": None}

    async def send(user_id, to_user_id, content, **_kw):
        return {
            "success": True,
            "message": {"id": 1, "conversation_id": 7, "content": content},
        }

    async def run_sync(fn, *args):
        return fn(*args)

    async def noop(*a, **k):
        return None

    quota = {"can_send": True, "remaining": 15, "limit": 20, "used": 5}
    monkeypatch.setattr(mod.messages_repo, "validate_message_send", valid)
    monkeypatch.setattr(mod.messages_repo, "send_message", send)
    monkeypatch.setattr(mod, "run_sync", run_sync)
    monkeypatch.setattr(mod, "get_user_membership", lambda uid: {"is_premium": False})
    monkeypatch.setattr(mod, "check_and_increment_message", lambda uid, p: quota)
    monkeypatch.setattr(mod.message_manager, "send_to_user", noop)
    monkeypatch.setattr(mod.notifications_repo, "notify_new_message", noop)

    result = await mod.send_message_endpoint.__wrapped__(
        request=MagicMock(),
        body=mod.SendMessageRequest(to_user_id="bob", content="hi"),
        current_user={"user_id": "me"},
    )
    assert result["message_limit"] == quota
