"""
對話置頂 API（c067）：社群列表的私訊與群組置頂＋拖曳排序（Telegram 模式）。

- 回應一律是整份最新的置頂清單 {"success": true, "pins": [{"kind", "id", "position"}]}，
  前端拿到就整份換掉，不用自己推算 position。
- 私訊列表看不到的對話（不是參與者、封鎖、整段刪除）／不在的群組一律 404 not_found，
  不透露存不存在。
- 上限、失效置頂清理、排序規則在 core/orm/chat_pins_repo。
- 群組置頂不看 group_chat_enabled 開關：開關關著時前端不顯示群組，置頂留著不影響。
- rate limit：@router 在上、@limiter 在下（反過來 limit 不會生效）。
"""

from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.chat_pins_repo import chat_pins_repo

router = APIRouter()

_INT_MAX = 2_147_483_647  # target_id 是 INTEGER，超過就是不存在（免得 DB 報 out of range 變 500）


class PinItem(BaseModel):
    kind: Literal["dm", "group"]
    id: int = Field(..., ge=1, le=_INT_MAX)


class PinOrderRequest(BaseModel):
    items: List[PinItem] = Field(..., max_length=20)


@router.get("/api/chat-pins")
@limiter.limit("60/minute")
async def list_chat_pins(
    request: Request, current_user: dict = Depends(get_current_user)
):
    return {
        "success": True,
        "pins": await chat_pins_repo.list_pins(current_user["user_id"]),
    }


# 註冊在 /{kind}/{target_id} 前面：層數不同本來就不會被吃掉，排前面只是讓人一眼看得出來
@router.put("/api/chat-pins/order")
@limiter.limit("30/minute")
async def reorder_chat_pins(
    request: Request,
    body: PinOrderRequest,
    current_user: dict = Depends(get_current_user),
):
    pins = await chat_pins_repo.reorder(
        current_user["user_id"], [(item.kind, item.id) for item in body.items]
    )
    return {"success": True, "pins": pins}


@router.put("/api/chat-pins/{kind}/{target_id}")
@limiter.limit("30/minute")
async def pin_chat(
    request: Request,
    kind: Literal["dm", "group"],
    target_id: int = Path(..., ge=1, le=_INT_MAX),
    current_user: dict = Depends(get_current_user),
):
    result = await chat_pins_repo.pin(current_user["user_id"], kind, target_id)
    if not result["success"]:
        error = result["error"]
        raise HTTPException(
            status_code=404 if error == "not_found" else 400, detail=error
        )
    return {"success": True, "pins": result["pins"]}


@router.delete("/api/chat-pins/{kind}/{target_id}")
@limiter.limit("30/minute")
async def unpin_chat(
    request: Request,
    kind: Literal["dm", "group"],
    target_id: int = Path(..., ge=1, le=_INT_MAX),
    current_user: dict = Depends(get_current_user),
):
    return {
        "success": True,
        "pins": await chat_pins_repo.unpin(current_user["user_id"], kind, target_id),
    }
