"""私訊收回真的收回（2026-09-29）。

收回原本只改 message_type，原文留在 DB，列表預覽、搜尋、讀取 API、通知都還吐得出來。
現在收回＝清空 content＋通知改「訊息已收回」，24 小時內才能收回。真 PostgreSQL，
交易內跑完 rollback，連不到就 skip。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

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
    users = {k: f"t-rc-{k}-{suffix}" for k in ("a", "b", "x")}
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


async def _send(s, frm, to, content):
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    sent = await messages_repo.send_message(frm, to, content, session=s)
    msg = sent["message"]
    await notifications_repo.notify_new_message(
        to,
        frm,
        frm,
        content,
        str(msg["conversation_id"]),
        message_id=msg["id"],
        session=s,
    )
    return msg


async def test_recall_clears_content_everywhere(pg_session):
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    s, u = pg_session
    secret = f"私密內容-{uuid.uuid4().hex[:6]}"
    msg = await _send(s, u["a"], u["b"], secret)

    result = await messages_repo.recall_message(msg["id"], u["a"], session=s)

    assert result["success"] is True
    assert result["conversation_id"] == msg["conversation_id"]
    assert result["to_user_id"] == u["b"]
    page = await messages_repo.get_messages(msg["conversation_id"], u["b"], session=s)
    assert page["messages"][-1]["message_type"] == "recalled"
    assert page["messages"][-1]["content"] == ""
    convs = await messages_repo.get_conversations(u["b"], session=s)
    assert convs[0]["last_message"] == ""
    assert convs[0]["last_message_type"] == "recalled"
    notes = await notifications_repo.get_notifications(u["b"], session=s)
    assert all(secret not in (n["body"] or "") for n in notes)
    assert notes[0]["data"]["recalled"] is True
    assert result["notifications"][0]["id"] == notes[0]["id"]


async def test_recall_only_rewrites_notification_showing_that_message(pg_session):
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    s, u = pg_session
    first = await _send(s, u["a"], u["b"], "第一則")
    await _send(s, u["a"], u["b"], "第二則")  # 合併通知現在顯示第二則

    result = await messages_repo.recall_message(first["id"], u["a"], session=s)

    assert result["notifications"] == []
    notes = await notifications_repo.get_notifications(u["b"], session=s)
    assert "第二則" in notes[0]["body"]


async def test_recall_matches_legacy_notification_without_message_id(pg_session):
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    s, u = pg_session
    sent = await messages_repo.send_message(u["a"], u["b"], "舊通知_%內容", session=s)
    msg = sent["message"]
    # 舊版通知沒有 message_id；內容含 LIKE 特殊字元，比對要 escape
    await notifications_repo.notify_new_message(
        u["b"], u["a"], "Alice", "舊通知_%內容", str(msg["conversation_id"]), session=s
    )

    result = await messages_repo.recall_message(msg["id"], u["a"], session=s)

    assert len(result["notifications"]) == 1
    assert result["notifications"][0]["body"] == "Alice: 訊息已收回"


async def test_recall_errors(pg_session):
    from core.orm.messages_repo import messages_repo

    s, u = pg_session
    msg = await _send(s, u["a"], u["b"], "hi")

    async def recall(uid, mid=msg["id"], **kw):
        return await messages_repo.recall_message(mid, uid, session=s, **kw)

    assert (await recall(u["b"]))["error"] == "permission_denied"
    assert (await recall(u["x"]))["error"] == "message_not_found"
    assert (await recall(u["a"], mid=10**9))["error"] == "message_not_found"
    late = datetime.now(timezone.utc) + timedelta(hours=24, minutes=1)
    assert (await recall(u["a"], now=late))["error"] == "recall_window_expired"
    assert (await recall(u["a"]))["success"] is True
    assert (await recall(u["a"]))["error"] == "already_recalled"


# ---------------------------------------------------------------------------
# Router：收回後推即時事件；搜尋排除已收回
# ---------------------------------------------------------------------------


async def test_recall_endpoint_pushes_events_and_maps_errors(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    import api.routers.messages as router

    pushed, updated = [], []

    async def fake_recall(message_id, user_id):
        if message_id == 2:
            return {"success": False, "error": "recall_window_expired"}
        return {
            "success": True,
            "recalled": True,
            "message_id": 1,
            "conversation_id": 9,
            "from_user_id": "a",
            "to_user_id": "b",
            "notifications": [{"id": "n1", "type": "message"}],
        }

    async def fake_send(user_id, payload):
        pushed.append((user_id, payload))

    async def fake_updated(user_id, notification):
        updated.append((user_id, notification))

    monkeypatch.setattr(router.messages_repo, "recall_message", fake_recall)
    monkeypatch.setattr(router.message_manager, "send_to_user", fake_send)
    monkeypatch.setattr(router, "push_notification_updated", fake_updated)
    req = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    endpoint = router.delete_message_endpoint.__wrapped__

    body = await endpoint(req, 1, {"user_id": "a"})

    assert body == {"success": True, "recalled": True}
    event = {"type": "message_recalled", "message_id": 1, "conversation_id": 9}
    assert ("a", event) in pushed and ("b", event) in pushed
    assert updated == [("b", {"id": "n1", "type": "message"})]
    with pytest.raises(HTTPException) as exc:
        await endpoint(req, 2, {"user_id": "a"})
    assert exc.value.status_code == 403
    assert exc.value.detail == "recall_window_expired"


def test_search_excludes_recalled_messages():
    from unittest.mock import MagicMock, patch

    from core.database.messages import search

    cur = MagicMock()
    cur.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cur
    with patch.object(search, "get_connection", return_value=conn):
        search.search_messages("u1", "hi")
    sql = cur.execute.call_args[0][0]
    assert "m.message_type <> 'recalled'" in sql


# ---------------------------------------------------------------------------
# c058：既有已收回訊息的原文與通知預覽補清
# ---------------------------------------------------------------------------


def _load_c058():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "c058_dm_recalled_content_purge.py"
    )
    spec = importlib.util.spec_from_file_location(
        "c058_dm_recalled_content_purge", path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_c058_purges_legacy_recalled_content(pg_session):
    from sqlalchemy import text

    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    s, u = pg_session
    long_text = "舊的已收回原文" * 10  # 超過 50 字，預覽帶 ...
    kept = await _send(s, u["a"], u["x"], "沒收回的要留著")
    for content in (long_text, "短_%"):
        sent = await messages_repo.send_message(u["a"], u["b"], content, session=s)
        msg = sent["message"]
        # 舊版：通知沒有 message_id、收回只改類型
        await notifications_repo.notify_new_message(
            u["b"], u["a"], "Alice", content, str(msg["conversation_id"]), session=s
        )
        await s.execute(
            text("UPDATE dm_messages SET message_type = 'recalled' WHERE id = :id"),
            {"id": msg["id"]},
        )
        await s.execute(
            text("UPDATE notifications SET is_read = TRUE WHERE user_id = :u"),
            {"u": u["b"]},
        )  # 讀過的不會被合併，兩則各自一筆

    for stmt in _load_c058().PURGE_SQL:
        await s.execute(text(stmt))

    contents = (
        (
            await s.execute(
                text(
                    "SELECT content FROM dm_messages WHERE from_user_id = :a AND to_user_id = :b"
                ),
                {"a": u["a"], "b": u["b"]},
            )
        )
        .scalars()
        .all()
    )
    bodies = (
        (
            await s.execute(
                text("SELECT body FROM notifications WHERE user_id = :u"), {"u": u["b"]}
            )
        )
        .scalars()
        .all()
    )
    untouched = (
        await s.execute(
            text("SELECT content FROM dm_messages WHERE id = :id"), {"id": kept["id"]}
        )
    ).scalar_one()
    assert contents == ["", ""]
    assert sorted(bodies) == ["Alice: 訊息已收回", "Alice: 訊息已收回"]
    assert untouched == "沒收回的要留著"


# ---------------------------------------------------------------------------
# 前端：24h、即時換列、列表預覽、官方確認框、通知原地改寫（node 實跑）
# ---------------------------------------------------------------------------


def test_frontend_recall_contract_node_gate():
    import subprocess
    from pathlib import Path

    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", str(repo / "tests" / "js" / "dm_recall.mjs")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(repo),
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


# ---------------------------------------------------------------------------
# 守衛：收回後，所有讀取路徑都拿不到原文（ORM 與舊 psycopg2 兩套都要打）
# 以後有人加新的讀取路徑，只要還是從 dm_messages.content 讀，就漏不出去；
# 有人把「清空 content」拿掉，這支會紅。資料要 commit（舊路徑自己開連線），結束時清掉。
# ---------------------------------------------------------------------------


async def test_recalled_text_unreachable_from_every_read_path():
    import json

    from core.database.connection import get_connection
    from core.database.messages.helpers import get_conversation_with_messages
    from core.database.messages.search import search_messages
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    try:
        conn = get_connection()
    except Exception as e:  # noqa: BLE001 — 連不到就 skip，不是測試失敗
        pytest.skip(f"PostgreSQL 連不到：{e}")
    suffix = uuid.uuid4().hex[:8]
    a, b = f"t-rg-a-{suffix}", f"t-rg-b-{suffix}"
    secret = f"guard{uuid.uuid4().hex[:10]}"
    try:
        c = conn.cursor()
        for uid in (a, b):
            c.execute(
                "INSERT INTO users (user_id, username) VALUES (%s, %s)", (uid, uid)
            )
        conn.commit()

        sent = await messages_repo.send_message(a, b, f"前情 {secret} 後續")
        msg = sent["message"]
        await notifications_repo.notify_new_message(
            b, a, a, msg["content"], str(msg["conversation_id"]), message_id=msg["id"]
        )
        assert (await messages_repo.recall_message(msg["id"], a))["success"] is True

        seen = {
            "orm.get_messages": await messages_repo.get_messages(
                msg["conversation_id"], b
            ),
            "orm.get_conversations": await messages_repo.get_conversations(b),
            "orm.get_notifications": await notifications_repo.get_notifications(b),
            "raw.with": get_conversation_with_messages(b, a),
            "raw.search": search_messages(b, secret),
        }
        for path, payload in seen.items():
            assert secret not in json.dumps(payload, ensure_ascii=False, default=str), (
                f"{path} 還拿得到已收回的原文"
            )
        assert seen["raw.with"]["messages"][-1]["message_type"] == "recalled"
    finally:
        c = conn.cursor()
        c.execute("DELETE FROM notifications WHERE user_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM dm_messages WHERE from_user_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM dm_conversations WHERE user1_id IN (%s, %s)", (a, b))
        c.execute("DELETE FROM users WHERE user_id IN (%s, %s)", (a, b))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# review 2026-09-29：舊通知用「結尾」比對會誤中（收回 "test" 時把 "prefix: test" 的
# 通知也改掉）；c058 不可逆，所以兩處都要完整比對 body
# ---------------------------------------------------------------------------


async def _legacy_pair(s, u):
    """兩則舊版訊息（通知沒有 message_id），第一則的內容剛好是第二則的結尾"""
    from sqlalchemy import text

    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    ids = []
    for content in ("test", "prefix: test"):
        sent = await messages_repo.send_message(u["a"], u["b"], content, session=s)
        msg = sent["message"]
        ids.append(msg["id"])
        await notifications_repo.notify_new_message(
            u["b"], u["a"], "Alice", content, str(msg["conversation_id"]), session=s
        )
        await s.execute(
            text("UPDATE notifications SET is_read = TRUE WHERE user_id = :u"),
            {"u": u["b"]},
        )  # 讀過的不合併，各自一筆
    return ids


async def test_recall_does_not_rewrite_notification_that_merely_ends_with_it(
    pg_session,
):
    from core.orm.messages_repo import messages_repo
    from core.orm.notifications_repo import notifications_repo

    s, u = pg_session
    first_id, _ = await _legacy_pair(s, u)

    result = await messages_repo.recall_message(first_id, u["a"], session=s)

    bodies = sorted(
        n["body"] for n in await notifications_repo.get_notifications(u["b"], session=s)
    )
    assert bodies == ["Alice: prefix: test", "Alice: 訊息已收回"], bodies
    assert len(result["notifications"]) == 1


async def test_c058_does_not_rewrite_notification_that_merely_ends_with_it(pg_session):
    from sqlalchemy import text

    s, u = pg_session
    first_id, _ = await _legacy_pair(s, u)
    await s.execute(
        text("UPDATE dm_messages SET message_type = 'recalled' WHERE id = :id"),
        {"id": first_id},
    )

    for stmt in _load_c058().PURGE_SQL:
        await s.execute(text(stmt))

    bodies = sorted(
        (
            await s.execute(
                text("SELECT body FROM notifications WHERE user_id = :u"), {"u": u["b"]}
            )
        )
        .scalars()
        .all()
    )
    assert bodies == ["Alice: prefix: test", "Alice: 訊息已收回"], bodies


async def test_recall_twice_is_409_already_recalled(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    import api.routers.messages as router

    async def fake_recall(message_id, user_id):
        return {"success": False, "error": "already_recalled"}

    monkeypatch.setattr(router.messages_repo, "recall_message", fake_recall)
    req = Request(
        {
            "type": "http",
            "method": "DELETE",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    with pytest.raises(HTTPException) as exc:
        await router.delete_message_endpoint.__wrapped__(req, 1, {"user_id": "a"})
    assert exc.value.status_code == 409
    assert exc.value.detail == "already_recalled"
