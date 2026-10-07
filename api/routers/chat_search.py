"""
聊天搜尋 API：一個框同時找人、群組、訊息（私訊＋群組）。

- 只搜自己看得到的，可見範圍規則在 core/orm/chat_search_repo.py。
- 群組開關（system_config.group_chat_enabled）關著：groups 與群組訊息回空陣列，私訊照常
  （不像 group_chat router 整組 404）。
- rate limit：@router 在上、@limiter 在下（反過來 limit 不會生效）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.chat_search_repo import QUERY_MAX, chat_search_repo
from core.orm.config_repo import config_repo

router = APIRouter()


@router.get("/api/chat-search")
@limiter.limit("30/minute")
async def chat_search(
    request: Request,
    # 原始長度先擋一個寬鬆上限；去頭尾空白後才照 1～QUERY_MAX 驗
    q: str = Query(..., max_length=200),
    current_user: dict = Depends(get_current_user),
):
    query = q.strip()
    if not 1 <= len(query) <= QUERY_MAX:
        raise HTTPException(status_code=400, detail="invalid_query")
    include_groups = bool(await config_repo.get_config("group_chat_enabled", False))
    result = await chat_search_repo.search(
        current_user["user_id"], query, include_groups=include_groups
    )
    return {"success": True, **result}
