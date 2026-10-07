"""功能選單的未讀標示（core/nav_badges.py，2026-10-02）。

社群＝私訊未讀＋群組未讀（靜音的不算，除非有人 @ 我）＋好友邀請＋群組邀請；
論壇＝有沒有未讀的留言類通知（有人留言你的文章／也在你留言過的文章留言；推不算）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def badges(monkeypatch):
    from core import nav_badges as mod

    state = {"groups_enabled": True}

    async def dm_unread(user_id):
        return 3

    async def pending(user_id):
        return 1

    async def flag(key, default=None, session=None):
        assert key == "group_chat_enabled"
        return state["groups_enabled"]

    async def groups(user_id):
        return [
            {"id": 1, "unread_count": 2, "muted": False},
            {"id": 2, "unread_count": 7, "muted": True},  # 靜音、沒被 @：不算
            {"id": 3, "unread_count": 4, "muted": True},  # 靜音但被 @：算
        ]

    async def mentions(user_id):
        return {3}

    async def invites(user_id):
        return [{"invite_id": 9}]

    async def forum(user_id):
        return True

    monkeypatch.setattr(mod.messages_repo, "get_unread_count", dm_unread)
    monkeypatch.setattr(mod.friends_repo, "get_pending_count", pending)
    monkeypatch.setattr(mod.config_repo, "get_config", flag)
    monkeypatch.setattr(mod.group_chat_repo, "list_groups", groups)
    monkeypatch.setattr(mod.group_chat_repo, "list_invites", invites)
    monkeypatch.setattr(mod.notifications_repo, "unread_mention_group_ids", mentions)
    monkeypatch.setattr(mod.notifications_repo, "has_unread_forum_activity", forum)
    mod._test_state = state
    return mod


async def test_social_counts_and_forum_flag(badges):
    result = await badges.compute_nav_badges("me")
    # 私訊 3 ＋ 群組（2 ＋ 被 @ 的靜音群 4）＋ 好友邀請 1 ＋ 群組邀請 1
    assert result == {"social": 11, "forum": True}


async def test_group_chat_flag_off_skips_groups(badges):
    badges._test_state["groups_enabled"] = False
    result = await badges.compute_nav_badges("me")
    assert result == {"social": 4, "forum": True}, "開關關著：群組與群組邀請都不算"


async def test_endpoint_returns_badges(monkeypatch):
    from starlette.requests import Request

    from api.routers import notifications as router

    async def fake(user_id):
        assert user_id == "me"
        return {"social": 2, "forum": False}

    monkeypatch.setattr(router, "compute_nav_badges", fake)
    req = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("t", 1),
        }
    )
    result = await router.get_nav_badges.__wrapped__(req, {"user_id": "me"})
    assert result == {"success": True, "social": 2, "forum": False}


def test_nav_badges_node_gate():
    """前端：社群數字（99+）、論壇紅點、選單看不到時入口紅點、訪客清空、API 失敗保留上次"""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        ["node", str(repo / "tests" / "js" / "nav_badges.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=repo,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "nav_badges: ok" in proc.stderr
