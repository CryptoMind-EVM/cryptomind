"""
對話自訂順序（c068）：社群列表「自訂順序」模式，私訊與群組共用一個序列，position 越小越上面。

- 顯示：置頂（chat_pins）永遠在最上面；其他先放還沒有位置的（新對話，照最後動態），再照 position。
  新訊息不會把對話往上推。排序模式（recent／custom）存在前端，這裡只存位置。
- 排序時前端只送「看得到、沒置頂、已載入」那一段的新順序。伺服器先把還沒有位置的看得到對話
  照最後動態補在最前面（第一次排＝整份快照，沒載入的自然接在後面），再把列到的放進它們
  原本佔的那幾格（同前端 chat-pins.js 的 mergeSubsetOrder），沒列到的位置不動，最後重編 0..n-1。
- 看得到＝私訊列表看得到（messages_repo.visible_conversation_clause）／還是群組成員，
  條件跟 chat_pins_repo 共用；失效的列（退群、群組解散、封鎖、整段刪除）每次排序時清掉。
  不認得、看不到的 id 直接略過（不能動到別人的對話）。
- 置頂的不會被自動補位置（照 chat_pins 排）；本來就有位置的置頂保留原位，取消置頂後回到那格。
- 同一個人的排序操作排隊（advisory lock）：兩個分頁同時拖曳不會互相蓋掉一半。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, List, Tuple

from sqlalchemy import and_, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .chat_pins_repo import _dm_accessible, _group_accessible
from .messages_repo import visible_conversation_clause
from .models import ChatOrder, ChatPin, DmConversation, GroupChat, GroupMember
from .session import using_session

MAX_ITEMS = 200  # 一次最多送幾項（router 也擋）
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


async def _lock(s: AsyncSession, user_id: str) -> None:
    """同一個人的排序操作排隊。交易結束自動釋放"""
    await s.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"chat-order:{user_id}")))
    )


def _not_pinned(user_id: str, kind: str, target_id):
    pinned = select(ChatPin.target_id).where(
        ChatPin.user_id == user_id, ChatPin.kind == kind, ChatPin.target_id == target_id
    )
    return ~pinned.exists()


def _no_position(user_id: str, kind: str, target_id):
    has = select(ChatOrder.target_id).where(
        ChatOrder.user_id == user_id,
        ChatOrder.kind == kind,
        ChatOrder.target_id == target_id,
    )
    return ~has.exists()


async def _prune_and_load(s: AsyncSession, user_id: str) -> List[Tuple[str, int]]:
    """清掉失效的列，回傳剩下的 (kind, target_id)（照 position 排）"""
    stale = or_(
        and_(
            ChatOrder.kind == "dm",
            ~_dm_accessible(user_id, ChatOrder.target_id).correlate(ChatOrder),
        ),
        and_(
            ChatOrder.kind == "group",
            ~_group_accessible(user_id, ChatOrder.target_id).correlate(ChatOrder),
        ),
    )
    await s.execute(
        delete(ChatOrder)
        .where(ChatOrder.user_id == user_id, stale)
        .execution_options(synchronize_session=False)
    )
    rows = await s.execute(
        select(ChatOrder.kind, ChatOrder.target_id)
        .where(ChatOrder.user_id == user_id)
        .order_by(ChatOrder.position, ChatOrder.kind, ChatOrder.target_id)
    )
    return [(kind, target_id) for kind, target_id in rows]


async def _unplaced(s: AsyncSession, user_id: str) -> List[Tuple[str, int]]:
    """看得到、沒置頂、還沒有位置的對話與群組，照最後動態（新的在前）"""
    dm_rows = await s.execute(
        select(
            DmConversation.id,
            func.coalesce(DmConversation.last_message_at, DmConversation.created_at),
        ).where(
            visible_conversation_clause(user_id),
            _not_pinned(user_id, "dm", DmConversation.id),
            _no_position(user_id, "dm", DmConversation.id),
        )
    )
    group_rows = await s.execute(
        select(
            GroupChat.id,
            func.coalesce(GroupChat.last_message_at, GroupChat.created_at),
        )
        .join(
            GroupMember,
            and_(GroupMember.group_id == GroupChat.id, GroupMember.user_id == user_id),
        )
        .where(
            _not_pinned(user_id, "group", GroupChat.id),
            _no_position(user_id, "group", GroupChat.id),
        )
    )
    items = [("dm", cid, at) for cid, at in dm_rows] + [
        ("group", gid, at) for gid, at in group_rows
    ]
    # 同一時間（幾乎不會）時新的 id 在前，結果固定
    items.sort(key=lambda it: (it[2] or _EPOCH, it[1]), reverse=True)
    return [(kind, target_id) for kind, target_id, _ in items]


def merge_subset_order(
    keys: List[Tuple[str, int]], subset: Iterable[Tuple[str, int]]
) -> List[Tuple[str, int]]:
    """列到的（去重、只算 keys 裡有的）照給的順序放進它們原本佔的那幾格，其他不動
    （同前端 chat-pins.js 的 mergeSubsetOrder）"""
    present = set(keys)
    listed = [k for k in dict.fromkeys(subset) if k in present]
    listed_set = set(listed)
    it = iter(listed)
    return [next(it) if k in listed_set else k for k in keys]


class ChatOrderRepository:
    async def reorder(
        self,
        user_id: str,
        items: Iterable[Tuple[str, int]],
        session: AsyncSession | None = None,
    ) -> List[dict]:
        """套用前端送來的部分順序，回傳整份最新位置 [{kind, id, position}]（照 position 排）"""
        async with using_session(session) as s:
            await _lock(s, user_id)
            placed = await _prune_and_load(s, user_id)
            # 還沒有位置的補在最前面（新對話在非置頂區最上面），再套用這次的拖曳
            keys = merge_subset_order(await _unplaced(s, user_id) + placed, items)
            # 整份重寫（幾百列以內）：比逐列比對簡單，也不用管 session 裡的舊物件
            await s.execute(
                delete(ChatOrder)
                .where(ChatOrder.user_id == user_id)
                .execution_options(synchronize_session=False)
            )
            if keys:
                await s.execute(
                    insert(ChatOrder),
                    [
                        {
                            "user_id": user_id,
                            "kind": kind,
                            "target_id": target_id,
                            "position": i,
                        }
                        for i, (kind, target_id) in enumerate(keys)
                    ],
                )
            return [
                {"kind": kind, "id": target_id, "position": i}
                for i, (kind, target_id) in enumerate(keys)
            ]

    async def clear(self, user_id: str, session: AsyncSession | None = None) -> None:
        """清掉自訂順序（回到全部「還沒排過」，自訂模式下等於照最後動態）"""
        async with using_session(session) as s:
            await _lock(s, user_id)
            await s.execute(
                delete(ChatOrder)
                .where(ChatOrder.user_id == user_id)
                .execution_options(synchronize_session=False)
            )


chat_order_repo = ChatOrderRepository()
