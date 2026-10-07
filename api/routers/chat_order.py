"""
對話自訂順序 API（c068）：社群列表「自訂順序」模式的位置（私訊與群組混排）。

- 排序模式（recent／custom）存在前端，GET /api/messages/conversations?order=custom 照這裡的位置排；
  每個私訊／群組都帶 order_position（還沒排過是 null）。置頂永遠在最上面（chat_pins）。
- PUT 送「看得到、沒置頂、已載入」那一段的新順序；看不到、不認得的直接略過（不透露存不存在）。
  回應是整份最新位置 {"success": true, "order": [{"kind", "id", "position"}]}，前端拿來更新
  已載入項目的 order_position，不用重抓。規則在 core/orm/chat_order_repo。
- DELETE 清掉自訂順序（重設）。
- rate limit：@router 在上、@limiter 在下（反過來 limit 不會生效）。
"""

from typing import List, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.chat_order_repo import MAX_ITEMS, chat_order_repo

router = APIRouter()

_INT_MAX = 2_147_483_647  # target_id 是 INTEGER，超過就是不存在（免得 DB 報 out of range 變 500）


class OrderItem(BaseModel):
    kind: Literal["dm", "group"]
    id: int = Field(..., ge=1, le=_INT_MAX)


class ChatOrderRequest(BaseModel):
    items: List[OrderItem] = Field(..., max_length=MAX_ITEMS)


@router.put("/api/chat-order")
@limiter.limit("30/minute")
async def reorder_chats(
    request: Request,
    body: ChatOrderRequest,
    current_user: dict = Depends(get_current_user),
):
    order = await chat_order_repo.reorder(
        current_user["user_id"], [(item.kind, item.id) for item in body.items]
    )
    return {"success": True, "order": order}


@router.delete("/api/chat-order")
@limiter.limit("10/minute")
async def reset_chat_order(
    request: Request, current_user: dict = Depends(get_current_user)
):
    await chat_order_repo.clear(current_user["user_id"])
    return {"success": True}
