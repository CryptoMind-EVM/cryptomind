"""
聊天室 AI 助理的問答紀錄（c070；2026-10-04 DANNY 決定存伺服器、跨裝置接續，
推翻 docs/plans/2026-10-01-chat-assistant-design.md 的「關掉就清空、不存資料庫」）。

- 每人每個聊天室只留最近 MAX_TURNS 則、RETENTION 天內；只有提問的人讀得到（讀取一律帶 user_id）。
- 一則問答存下它讀過的訊息 id（source_ids）。那些訊息任一則被收回、被提問者自己刪掉，
  或提問者退群／被移出／群解散／刪掉對話 → 這則（或整個聊天室的紀錄）當場刪掉，
  跟收回／退群在同一個交易裡（呼叫端傳 session）。別人收回的內容不會因為「AI 整理過」留下來。
- 存的時候要先鎖住來源訊息（FOR SHARE）再確認沒人收回：產生答案要花幾十秒，這中間的收回
  會跟這裡排隊——收回先 commit，這裡看得到就不存；這裡先鎖，收回的 UPDATE 等到存完，
  它的 DELETE 就看得到這一列。
- target_id 是多型（dm_conversations.id／group_chats.id），不設 FK；users 刪除時 CASCADE。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.ai_card import CARD_MAX_LENGTH

from .models import ChatAssistantHistory as History
from .models import DmConversation, DmMessage, GroupMessage
from .session import using_session

MAX_TURNS = 10
RETENTION = timedelta(days=7)

_NOT_FOUND = {"success": False, "error": "not_found"}


def _turn(row: History) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "answer": row.answer,
        "meta": row.meta or {},
        "created_at": row.created_at,
    }


async def _can_see(s: AsyncSession, kind: str, target_id: int, user_id: str) -> bool:
    """提問者現在還看得到這個聊天室（私訊參與者／群成員）"""
    if kind == "dm":
        found = await s.scalar(
            select(DmConversation.id).where(
                DmConversation.id == target_id,
                (DmConversation.user1_id == user_id)
                | (DmConversation.user2_id == user_id),
            )
        )
        return found is not None
    if kind == "group":
        # group_chat_repo 也會 import 這支，延後 import 避免循環
        from .group_chat_repo import is_member

        return await is_member(s, target_id, user_id) is not None
    return False


class ChatAssistantHistoryRepository:
    async def list_turns(
        self,
        kind: str,
        target_id: int,
        user_id: str,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> dict:
        """由舊到新。聊天室已經看不到（退群、不是自己的對話）→ not_found，紀錄不給。"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            if not await _can_see(s, kind, target_id, user_id):
                return _NOT_FOUND
            rows = (
                await s.scalars(
                    select(History)
                    .where(
                        History.user_id == user_id,
                        History.kind == kind,
                        History.target_id == target_id,
                        History.created_at >= now - RETENTION,
                    )
                    .order_by(History.id.desc())
                    .limit(MAX_TURNS)
                )
            ).all()
        return {"success": True, "turns": [_turn(r) for r in reversed(rows)]}

    async def get_turn_for_share(
        self,
        kind: str,
        target_id: int,
        user_id: str,
        turn_id: int,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        """要分享成卡片的那則問答（{id, answer}）。一定要是自己問的、就在這個聊天室、
        聊天室現在還看得到、沒過期；任一不符回 None（呼叫端一律當 not_found，不洩漏別人的 turn id）。
        答案由伺服器取，卡片內容不接受呼叫端傳來的文字。"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            if not await _can_see(s, kind, target_id, user_id):
                return None
            row = await s.scalar(
                select(History).where(
                    History.id == turn_id,
                    History.user_id == user_id,
                    History.kind == kind,
                    History.target_id == target_id,
                    History.created_at >= now - RETENTION,
                )
            )
            if row is None or not (row.answer or "").strip():
                return None
            return {"id": row.id, "answer": row.answer[:CARD_MAX_LENGTH]}

    async def dm_conversation_id(
        self, user_id: str, other_user_id: str, session: AsyncSession | None = None
    ) -> Optional[int]:
        """兩人的私訊對話 id（歷史表的 target_id 存的是對話 id，不是對方的 user_id）"""
        user1_id, user2_id = sorted((user_id, other_user_id))
        async with using_session(session) as s:
            return await s.scalar(
                select(DmConversation.id).where(
                    DmConversation.user1_id == user1_id,
                    DmConversation.user2_id == user2_id,
                )
            )

    async def save_turn(
        self,
        kind: str,
        target_id: int,
        user_id: str,
        question: str,
        answer: str,
        source_ids: Iterable[int],
        meta: dict,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> Optional[dict]:
        """存一則並把這個聊天室超過 MAX_TURNS 的舊的刪掉。來源訊息已被收回／聊天室已看不到 → 不存，回 None。"""
        ids = sorted(set(source_ids))
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            if not await _can_see(s, kind, target_id, user_id):
                return None
            if ids:
                model = DmMessage if kind == "dm" else GroupMessage
                intact = (
                    await s.scalars(
                        select(model.id)
                        .where(model.id.in_(ids), model.message_type != "recalled")
                        .with_for_update(read=True)
                    )
                ).all()
                if len(intact) != len(ids):
                    return None
            row = History(
                user_id=user_id,
                kind=kind,
                target_id=target_id,
                question=question,
                answer=answer,
                source_ids=ids,
                meta=meta,
                created_at=now,
            )
            s.add(row)
            await s.flush()
            keep = (
                select(History.id)
                .where(
                    History.user_id == user_id,
                    History.kind == kind,
                    History.target_id == target_id,
                )
                .order_by(History.id.desc())
                .limit(MAX_TURNS)
            )
            await s.execute(
                delete(History).where(
                    History.user_id == user_id,
                    History.kind == kind,
                    History.target_id == target_id,
                    History.id.not_in(keep),
                )
            )
            return _turn(row)

    async def delete_turn(
        self, turn_id: int, user_id: str, session: AsyncSession | None = None
    ) -> bool:
        async with using_session(session) as s:
            result = await s.execute(
                delete(History).where(History.id == turn_id, History.user_id == user_id)
            )
            return result.rowcount > 0

    async def clear(
        self,
        kind: str,
        target_id: int,
        user_id: str,
        session: AsyncSession | None = None,
    ) -> int:
        return await self.invalidate_chat(
            kind, target_id, user_id=user_id, session=session
        )

    # ── 作廢：收回、隱藏、退群、解散時由各 repo 在同一個交易裡呼叫 ──────────────────

    async def invalidate_message(
        self,
        kind: str,
        message_id: int,
        *,
        user_id: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """讀過這則訊息的問答全刪（收回：所有人的；自己隱藏：只刪自己的）。訊息 id 在各自的表裡唯一，
        所以不用帶 target_id。"""
        stmt = delete(History).where(
            History.kind == kind, History.source_ids.contains([message_id])
        )
        if user_id is not None:
            stmt = stmt.where(History.user_id == user_id)
        async with using_session(session) as s:
            return (await s.execute(stmt)).rowcount

    async def invalidate_chat(
        self,
        kind: str,
        target_id: int,
        *,
        user_id: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """整個聊天室的紀錄全刪（user_id 給了只刪那個人的：退群、被移出、刪對話；不給＝所有人：解散、群刪除）"""
        stmt = delete(History).where(
            History.kind == kind, History.target_id == target_id
        )
        if user_id is not None:
            stmt = stmt.where(History.user_id == user_id)
        async with using_session(session) as s:
            return (await s.execute(stmt)).rowcount

    async def purge_expired(
        self,
        *,
        now: Optional[datetime] = None,
        session: AsyncSession | None = None,
    ) -> int:
        """每日排程：超過保留天數的刪掉（讀的時候也會過濾，這裡是真的清掉）"""
        now = now or datetime.now(timezone.utc)
        async with using_session(session) as s:
            result = await s.execute(
                delete(History).where(History.created_at < now - RETENTION)
            )
            return result.rowcount


chat_assistant_history_repo = ChatAssistantHistoryRepository()
