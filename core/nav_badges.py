"""
功能選單的未讀標示（2026-10-02）：需要你回應的才掛數字，不急的用紅點，工具類不掛。

- 社群（數字）＝私訊未讀＋群組未讀＋好友邀請＋群組邀請。靜音的群不算，除非有人 @ 我
  （跟社群頁「訊息」分頁的數字同一條規則，friends.js loadConversations）。
- 論壇（紅點）＝有沒有未讀的留言類通知：有人留言你的文章、也在你留言過的文章留言。推不算。
"""

from __future__ import annotations

from core.orm.config_repo import config_repo
from core.orm.friends_repo import friends_repo
from core.orm.group_chat_repo import group_chat_repo
from core.orm.messages_repo import messages_repo
from core.orm.notifications_repo import notifications_repo


async def compute_nav_badges(user_id: str) -> dict:
    social = await messages_repo.get_unread_count(user_id)
    social += await friends_repo.get_pending_count(user_id)
    if await config_repo.get_config("group_chat_enabled", False):
        mentioned = await notifications_repo.unread_mention_group_ids(user_id)
        social += sum(
            int(g.get("unread_count") or 0)
            for g in await group_chat_repo.list_groups(user_id)
            if not g.get("muted") or g["id"] in mentioned
        )
        social += len(await group_chat_repo.list_invites(user_id))
    forum = await notifications_repo.has_unread_forum_activity(user_id)
    return {"social": int(social), "forum": bool(forum)}
