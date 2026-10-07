"""AI 回答快照分享的資料層（core/orm/answer_share_repo.py、c072）。

真 PostgreSQL、整段交易最後 rollback（同 test_chat_assistant_history）。重點：token 只存 hash、
過期／撤銷後讀不到、只有自己看得到／撤得掉自己的、每日上限、使用者刪除時一起刪。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from core import answer_share as core
from tests import group_chat_pg

pytestmark = pytest.mark.unit
gc_pg = group_chat_pg.gc_pg

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _repo():
    from core.orm.answer_share_repo import AnswerShareRepository

    return AnswerShareRepository()


# ── 三處同步（migration／init_db／ORM）────────────────────────────────────


def test_migration_schema_and_models_in_sync():
    src = _read("alembic/versions/c072_shared_answers.py")
    assert re.search(r'^revision = "c072"$', src, re.M)
    assert re.search(r'^down_revision = "c071"$', src, re.M)
    upgrade, downgrade = src.split("def downgrade")
    assert "CREATE TABLE IF NOT EXISTS shared_answers" in upgrade
    assert "token_hash" in upgrade and "UNIQUE" in upgrade
    assert "ON DELETE CASCADE" in upgrade
    assert "DROP TABLE IF EXISTS shared_answers" in downgrade
    # 只加不改：沒有任何 ALTER／DROP（除了 downgrade）
    assert "ALTER " not in upgrade.upper().replace("CREATE TABLE", "")

    schema = _read("core/database/schema.py")
    assert '("shared_answers", create_shared_answers_table)' in schema
    assert "CREATE TABLE IF NOT EXISTS shared_answers" in schema
    models = _read("core/orm/models.py")
    assert '__tablename__ = "shared_answers"' in models
    # token 本身不存（資料庫外洩也拿不到可用連結）
    assert "token_hash" in models and not re.search(r"\btoken:\s*Mapped", models)


# ── 行為 ─────────────────────────────────────────────────────────────────


async def test_create_stores_only_the_hash_and_reads_back(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    made = await r.create(u["a"], "BTC 怎麼看", "整理中", now=NOW, session=s)
    assert core.valid_token_shape(made["token"])
    assert made["expires_at"] == NOW + timedelta(days=core.TTL_DAYS)
    stored = (
        await s.execute(
            text("SELECT token_hash, question, answer FROM shared_answers WHERE id=:i"),
            {"i": made["id"]},
        )
    ).one()
    assert (
        stored.token_hash == core.hash_token(made["token"])
        and made["token"] not in stored.token_hash
    )
    got = await r.get_active(made["token"], now=NOW, session=s)
    assert got == {"question": "BTC 怎麼看", "answer": "整理中", "created_at": NOW}


async def test_unknown_malformed_expired_and_revoked_are_all_none(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    made = await r.create(u["a"], "q", "a", now=NOW, session=s)
    assert await r.get_active(core.new_token(), now=NOW, session=s) is None
    assert await r.get_active("short", now=NOW, session=s) is None
    # 過期
    assert (
        await r.get_active(
            made["token"], now=NOW + timedelta(days=core.TTL_DAYS, seconds=1), session=s
        )
        is None
    )
    assert (
        await r.get_active(
            made["token"], now=NOW + timedelta(days=core.TTL_DAYS - 1), session=s
        )
        is not None
    )
    # 撤銷
    assert await r.revoke(u["a"], made["id"], now=NOW, session=s) is True
    assert await r.get_active(made["token"], now=NOW, session=s) is None


async def test_only_the_owner_can_revoke_or_list(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    mine = await r.create(u["a"], "q1", "a1", now=NOW, session=s)
    await r.create(u["b"], "q2", "a2", now=NOW, session=s)
    assert (
        await r.revoke(u["b"], mine["id"], now=NOW, session=s) is False
    )  # 別人的撤不掉
    assert await r.get_active(mine["token"], now=NOW, session=s) is not None
    assert [x["question"] for x in await r.list_active(u["a"], now=NOW, session=s)] == [
        "q1"
    ]
    assert await r.revoke(u["a"], 999999999, now=NOW, session=s) is False
    # 重複撤銷：第二次沒東西可撤
    assert await r.revoke(u["a"], mine["id"], now=NOW, session=s) is True
    assert await r.revoke(u["a"], mine["id"], now=NOW, session=s) is False
    assert await r.list_active(u["a"], now=NOW, session=s) == []


async def test_list_hides_expired_and_never_exposes_token_or_hash(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    await r.create(u["a"], "old", "a", now=NOW - timedelta(days=40), session=s)
    await r.create(u["a"], "new", "a", now=NOW, session=s)
    items = await r.list_active(u["a"], now=NOW, session=s)
    assert [x["question"] for x in items] == ["new"]
    assert set(items[0]) == {"id", "question", "created_at", "expires_at"}


async def test_daily_count_includes_revoked_and_ignores_older(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    first = await r.create(u["a"], "q", "a", now=NOW - timedelta(hours=2), session=s)
    await r.create(u["a"], "q", "a", now=NOW - timedelta(hours=30), session=s)
    await r.revoke(
        u["a"], first["id"], now=NOW, session=s
    )  # 撤銷也算今天用掉一次（不能靠撤銷繞過上限）
    assert await r.count_recent(u["a"], now=NOW, session=s) == 1
    await r.create(u["b"], "q", "a", now=NOW, session=s)
    assert await r.count_recent(u["a"], now=NOW, session=s) == 1


async def test_purge_removes_long_expired_rows_only(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    gone = await r.create(u["a"], "gone", "a", now=NOW - timedelta(days=60), session=s)
    kept = await r.create(
        u["a"], "recently expired", "a", now=NOW - timedelta(days=33), session=s
    )
    live = await r.create(u["a"], "live", "a", now=NOW, session=s)
    assert await r.purge_expired(now=NOW, session=s) == 1
    ids = {
        row[0]
        for row in (
            await s.execute(
                text("SELECT id FROM shared_answers WHERE user_id=:u"), {"u": u["a"]}
            )
        ).all()
    }
    assert ids == {kept["id"], live["id"]} and gone["id"] not in ids


async def test_deleting_the_user_deletes_their_shares(gc_pg):
    s, u, _ = gc_pg
    r = _repo()
    made = await r.create(u["a"], "q", "a", now=NOW, session=s)
    await s.execute(text("DELETE FROM users WHERE user_id=:u"), {"u": u["a"]})
    assert await r.get_active(made["token"], now=NOW, session=s) is None


async def test_limits_are_enforced_inside_the_create_transaction(gc_pg):
    from core.orm.answer_share_repo import ShareLimitError

    s, u, _ = gc_pg
    r = _repo()
    await r.create(u["a"], "q", "a", now=NOW, session=s, daily_limit=2, max_active=10)
    second = await r.create(
        u["a"], "q", "a", now=NOW, session=s, daily_limit=2, max_active=10
    )
    with pytest.raises(ShareLimitError) as exc:
        await r.create(
            u["a"], "q", "a", now=NOW, session=s, daily_limit=2, max_active=10
        )
    assert exc.value.kind == "daily"
    # 撤銷不能繞過每日上限
    await r.revoke(u["a"], second["id"], now=NOW, session=s)
    with pytest.raises(ShareLimitError):
        await r.create(
            u["a"], "q", "a", now=NOW, session=s, daily_limit=2, max_active=10
        )
    # 別人不受影響
    await r.create(u["b"], "q", "a", now=NOW, session=s, daily_limit=2, max_active=10)


async def test_active_cap_counts_only_live_links(gc_pg):
    from core.orm.answer_share_repo import ShareLimitError

    s, u, _ = gc_pg
    r = _repo()
    old = [
        await r.create(u["a"], "q", "a", now=NOW - timedelta(days=3 + i), session=s)
        for i in range(2)
    ]
    # 隔天的額度已重算（上限只看近 24 小時），但有效連結總數仍受 max_active 限制
    with pytest.raises(ShareLimitError) as exc:
        await r.create(
            u["a"], "q", "a", now=NOW, session=s, daily_limit=50, max_active=2
        )
    assert exc.value.kind == "active"
    await r.revoke(u["a"], old[0]["id"], now=NOW, session=s)
    await r.create(u["a"], "q", "a", now=NOW, session=s, daily_limit=50, max_active=2)
    assert await r.count_active(u["a"], now=NOW, session=s) == 2


async def test_list_returns_up_to_the_active_cap(gc_pg):
    from core.orm.answer_share_repo import _LIST_LIMIT

    assert _LIST_LIMIT >= core.MAX_ACTIVE, (
        "清單要放得下所有有效連結，否則第 N+1 筆撤銷不了"
    )
