"""群組聊天測試共用：真 PostgreSQL、整段交易最後 rollback，連不到就 skip（同 test_dm_reactions）。

用法::

    from tests import group_chat_pg
    gc_pg = group_chat_pg.gc_pg   # 共用 fixture

    async def test_x(gc_pg):
        s, u, make = gc_pg          # u["a"]…u["f"] 是 Pro 使用者；make 有 friends／block／free 等工具
"""

from __future__ import annotations

import os
import uuid

import pytest


class _Maker:
    def __init__(self, session, suffix):
        self.s = session
        self.suffix = suffix

    async def user(self, key: str, premium: bool = True) -> str:
        from sqlalchemy import text

        uid = f"t-gc-{key}-{self.suffix}"
        await self.s.execute(
            text(
                "INSERT INTO users (user_id, username, membership_tier, membership_expires_at) "
                "VALUES (:u, :u, :tier, NULL)"
            ),
            {"u": uid, "tier": "premium" if premium else "free"},
        )
        return uid

    async def friends(self, a: str, *others: str) -> None:
        from sqlalchemy import text

        for b in others:
            await self.s.execute(
                text(
                    "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'accepted')"
                ),
                {"a": a, "b": b},
            )

    async def block(self, a: str, b: str) -> None:
        from sqlalchemy import text

        await self.s.execute(
            text(
                "DELETE FROM friendships WHERE (user_id=:a AND friend_id=:b) OR (user_id=:b AND friend_id=:a)"
            ),
            {"a": a, "b": b},
        )
        await self.s.execute(
            text(
                "INSERT INTO friendships (user_id, friend_id, status) VALUES (:a, :b, 'blocked')"
            ),
            {"a": a, "b": b},
        )

    async def set_premium(self, uid: str, premium: bool) -> None:
        from sqlalchemy import text

        await self.s.execute(
            text("UPDATE users SET membership_tier=:t WHERE user_id=:u"),
            {"t": "premium" if premium else "free", "u": uid},
        )

    async def config(self, key: str, value: str) -> None:
        from sqlalchemy import text

        # 測試庫不一定有種子列：upsert（交易最後 rollback，不會留下來）
        await self.s.execute(
            text(
                "INSERT INTO system_config (key, value, value_type, category) VALUES (:k, :v, 'int', 'limits') "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"v": value, "k": key},
        )


@pytest.fixture
async def gc_pg():
    from dotenv import load_dotenv
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
    make = _Maker(session, uuid.uuid4().hex[:8])
    users = {k: await make.user(k) for k in "abcdef"}
    try:
        yield session, users, make
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()
