"""群組聊天 schema（c066，設計 docs/plans/2026-10-01-group-chat-design.md）。

六張 group_* 表要同時出現在 migration、啟動 reconcile（schema.py）、ORM models 三處；
功能開關與四個上限要有 system_config 種子。真 PG 部分驗約束（同群同人只能一張待處理邀請、訊息類型白名單）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import group_chat_pg

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
gc_pg = group_chat_pg.gc_pg  # 共用 fixture（真 PG、rollback）
TABLES = (
    "group_chats",
    "group_members",
    "group_messages",
    "group_message_reactions",
    "group_invites",
    "group_reports",
)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_migration_chain_and_tables():
    src = _read("alembic/versions/c066_group_chat.py")
    assert re.search(r'^revision = "c066"$', src, re.M)
    assert re.search(r'^down_revision = "c065"$', src, re.M)
    downgrade = src[src.index("def downgrade") :]
    assert "DROP TABLE IF EXISTS" in downgrade
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in src, table
        assert f'"{table}"' in downgrade, f"downgrade 要刪 {table}"


def test_schema_reconcile_and_models_have_tables():
    schema = _read("core/database/schema.py")
    assert '("group_chat", create_group_chat_tables)' in schema
    models = _read("core/orm/models.py")
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema, table
        assert f'__tablename__ = "{table}"' in models, table


def test_config_seeds():
    schema = _read("core/database/schema.py")
    for key, value in (
        ("group_chat_enabled", "false"),
        ("limit_group_create", "5"),
        ("limit_group_join", "20"),
        ("limit_group_members", "50"),
        ("limit_daily_group_invites", "30"),
    ):
        assert re.search(rf'"{key}",\s*"{value}"', schema), key


async def test_constraints(gc_pg):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    s, u, _ = gc_pg
    gid = (
        await s.execute(
            text(
                "INSERT INTO group_chats (name, owner_id) VALUES ('投資閒聊', :o) RETURNING id"
            ),
            {"o": u["a"]},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO group_members (group_id, user_id) VALUES (:g, :u)"),
        {"g": gid, "u": u["a"]},
    )
    mid = (
        await s.execute(
            text(
                "INSERT INTO group_messages (group_id, from_user_id, content) VALUES (:g, :u, 'hi') RETURNING id"
            ),
            {"g": gid, "u": u["a"]},
        )
    ).scalar_one()
    assert mid > 0

    invite = "INSERT INTO group_invites (group_id, inviter_id, invitee_id) VALUES (:g, :a, :b)"
    await s.execute(text(invite), {"g": gid, "a": u["a"], "b": u["b"]})
    nested = await s.begin_nested()
    with pytest.raises(IntegrityError):
        await s.execute(text(invite), {"g": gid, "a": u["a"], "b": u["b"]})
    await nested.rollback()

    # 處理掉的邀請不算：同一人可以再被邀一次
    await s.execute(
        text("UPDATE group_invites SET status='declined' WHERE group_id=:g"), {"g": gid}
    )
    await s.execute(text(invite), {"g": gid, "a": u["a"], "b": u["b"]})

    nested = await s.begin_nested()
    with pytest.raises(IntegrityError):
        await s.execute(
            text(
                "INSERT INTO group_messages (group_id, from_user_id, content, message_type) "
                "VALUES (:g, :u, 'x', 'sticker')"
            ),
            {"g": gid, "u": u["a"]},
        )
    await nested.rollback()

    nested = await s.begin_nested()
    with pytest.raises(IntegrityError):
        await s.execute(
            text("INSERT INTO group_chats (name, owner_id) VALUES ('', :o)"),
            {"o": u["a"]},
        )
    await nested.rollback()
