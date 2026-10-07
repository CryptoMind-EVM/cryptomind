"""
對話置頂（c067）：社群列表的私訊與群組共用一張 chat_pins，position 越小越上面。

- 上限 MAX_PINS 是私訊＋群組合計。
- 每次操作先清掉失效的置頂，不佔名額、不擋排序，清完重編成 0..n-1。失效＝退群／被踢，
  或私訊列表看不到（不是參與者、封鎖、整段刪除；條件跟 get_conversations 共用
  messages_repo.visible_conversation_clause）。解除封鎖或對方再傳訊息後要重新置頂。
- 同一個人的置頂操作排隊（advisory lock）：兩個分頁同時置頂不會超過上限、position 不會撞。
- 排序是寬鬆的：沒置頂的、不認得的直接略過，沒列到的置頂保持原本相對順序接在後面
  （前端拖曳時手上的清單可能比伺服器舊一拍，不該因此整個失敗）。
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

from sqlalchemy import and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .messages_repo import visible_conversation_clause
from .models import ChatPin, DmConversation, GroupMember
from .session import using_session

MAX_PINS = 10
KINDS = ("dm", "group")


def _pin_dict(pin: ChatPin) -> dict:
    return {"kind": pin.kind, "id": pin.target_id, "position": pin.position}


async def _lock(s: AsyncSession, user_id: str) -> None:
    """同一個人的置頂操作排隊。交易結束自動釋放"""
    await s.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"chat-pins:{user_id}")))
    )


def _dm_accessible(user_id: str, target_id):
    """私訊列表看得到這段對話（同 get_conversations）"""
    return exists(
        select(DmConversation.id).where(
            DmConversation.id == target_id, visible_conversation_clause(user_id)
        )
    )


def _group_accessible(user_id: str, target_id):
    return exists(
        select(GroupMember.group_id).where(
            GroupMember.group_id == target_id, GroupMember.user_id == user_id
        )
    )


async def _accessible(s: AsyncSession, user_id: str, kind: str, target_id: int) -> bool:
    check = _dm_accessible if kind == "dm" else _group_accessible
    return bool((await s.execute(select(check(user_id, target_id)))).scalar())


async def _load(s: AsyncSession, user_id: str) -> List[ChatPin]:
    """清掉失效的置頂，回傳剩下的（照 position 排）"""
    stale = or_(
        and_(
            ChatPin.kind == "dm",
            ~_dm_accessible(user_id, ChatPin.target_id).correlate(ChatPin),
        ),
        and_(
            ChatPin.kind == "group",
            ~_group_accessible(user_id, ChatPin.target_id).correlate(ChatPin),
        ),
    )
    await s.execute(
        delete(ChatPin)
        .where(ChatPin.user_id == user_id, stale)
        .execution_options(synchronize_session=False)
    )
    rows = await s.execute(
        select(ChatPin)
        .where(ChatPin.user_id == user_id)
        .order_by(ChatPin.position, ChatPin.created_at, ChatPin.kind, ChatPin.target_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _save(s: AsyncSession, pins: List[ChatPin]) -> List[dict]:
    """依清單順序重編 position 成 0..n-1"""
    for i, pin in enumerate(pins):
        if pin.position != i:
            pin.position = i
    await s.flush()
    return [_pin_dict(p) for p in pins]


class ChatPinsRepository:
    async def list_pins(
        self, user_id: str, session: AsyncSession | None = None
    ) -> List[dict]:
        async with using_session(session) as s:
            await _lock(s, user_id)
            return await _save(s, await _load(s, user_id))

    async def pin(
        self,
        user_id: str,
        kind: str,
        target_id: int,
        session: AsyncSession | None = None,
    ) -> dict:
        """置頂（接在最後面）；已置頂就原樣回傳。列表看不到的對話／不在的群組一律 not_found"""
        if kind not in KINDS:
            return {"success": False, "error": "invalid_kind"}
        async with using_session(session) as s:
            await _lock(s, user_id)
            pins = await _load(s, user_id)
            if not await _accessible(s, user_id, kind, target_id):
                return {"success": False, "error": "not_found"}
            if not any(p.kind == kind and p.target_id == target_id for p in pins):
                if len(pins) >= MAX_PINS:
                    return {"success": False, "error": "pin_limit_reached"}
                pin = ChatPin(
                    user_id=user_id, kind=kind, target_id=target_id, position=len(pins)
                )
                s.add(pin)
                pins.append(pin)
            return {"success": True, "pins": await _save(s, pins)}

    async def unpin(
        self,
        user_id: str,
        kind: str,
        target_id: int,
        session: AsyncSession | None = None,
    ) -> List[dict]:
        """取消置頂（本來就沒置頂也不算錯）"""
        async with using_session(session) as s:
            await _lock(s, user_id)
            remaining = []
            for pin in await _load(s, user_id):
                if pin.kind == kind and pin.target_id == target_id:
                    await s.delete(pin)
                else:
                    remaining.append(pin)
            return await _save(s, remaining)

    async def reorder(
        self,
        user_id: str,
        items: Iterable[Tuple[str, int]],
        session: AsyncSession | None = None,
    ) -> List[dict]:
        """items 裡有置頂的照給的順序排在前面，沒列到的置頂保持相對順序接在後面"""
        async with using_session(session) as s:
            await _lock(s, user_id)
            pins = await _load(s, user_id)
            by_key = {(p.kind, p.target_id): p for p in pins}
            listed = [by_key[k] for k in dict.fromkeys(items) if k in by_key]
            rest = [p for p in pins if p not in listed]
            return await _save(s, listed + rest)


chat_pins_repo = ChatPinsRepository()
