"""
聊天室 AI 助理 API（設計 docs/plans/2026-10-01-chat-assistant-design.md）

- 開關 system_config.chat_assistant_enabled 關著整組 404（router 層 dependency）。
- 只讀提問者自己看得到的訊息（core/orm/chat_assistant_repo）。
- 問答存伺服器、跨裝置接續（2026-10-04 DANNY，core/orm/chat_assistant_history_repo）：
  每人每個聊天室留最近 10 則、7 天，只有提問的人讀得到；來源訊息被收回／退群就作廢。
  產生答案在背景任務裡跑，關掉抽屜（斷線）不會中斷：答案照樣存起來，回來打開就在。
- 誰能用：私訊＝對話參與者；群組＝成員且目前是 Pro。非 Pro 私訊每天 limit_ai_assistant_free_daily 次
  （只算平台模型；自帶 key 不計）。
- DANNY：不限次數但一定要限頻率——
  每人 3/分鐘＋30/小時（含自帶 key）；同一人同時一題；平台模型全站同時 CHAT_ASSISTANT_PLATFORM_CONCURRENCY
  題（預設 1，另一個位置留給 AI 聊天），排隊最多 QUEUE_WAIT_SECONDS 秒。
  同時一題與排隊都是行程內狀態：正式站 WEB_CONCURRENCY=1；改多 worker 要一起改走 Redis。
- 失敗（排隊逾時、模型錯誤、斷線）退回已扣的次數。自帶 key 失敗不改用平台模型。
- rate limit：@router 在上、@limiter 在下（反過來 limit 不會生效）。
"""

import asyncio
import json
import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.user_llm import resolve_user_llm_credentials, select_user_llm_model
from api.utils import logger, run_sync
from core.agents.fallback import fallback_credentials
from core.chat_assistant.answer import (
    ANSWER_MAX_TOKENS,
    LANGUAGE_NAMES,
    AssistantFailed,
    answer,
    condense_chunks,
    context_text,
)
from core.chat_assistant.context import (
    format_line,
    plan_context,
    resolve_tz,
    trim_history,
)
from core.database import get_user_membership
from core.model_config import PLATFORM_FREE_MODEL_LABEL, is_local_provider
from core.orm.chat_assistant_history_repo import chat_assistant_history_repo
from core.orm.chat_assistant_repo import chat_assistant_repo
from core.orm.config_repo import config_repo
from utils.user_client_factory import create_user_llm_client

TOTAL_BUDGET_SECONDS = 120.0  # gunicorn timeout 180 秒
CONDENSE_BUDGET_SECONDS = 60.0
QUEUE_WAIT_SECONDS = 20.0
IN_FLIGHT_STALE_SECONDS = (
    TOTAL_BUDGET_SECONDS + 30
)  # 回應沒被讀（斷線）時 finally 不會跑，過期就不算
DEFAULT_FREE_DAILY = 5


def _platform_concurrency() -> int:
    try:
        return max(1, int(os.getenv("CHAT_ASSISTANT_PLATFORM_CONCURRENCY", "1")))
    except ValueError:
        return 1


_PLATFORM_SEM = asyncio.Semaphore(_platform_concurrency())
_IN_FLIGHT: dict[str, float] = {}  # user_id → 開始時間（loop.time）
# user_id → 正在產生的那題（抽屜重開時 GET /history 告訴前端「還在整理」）
_PENDING: dict[str, dict] = {}
_TASKS: set[asyncio.Task] = set()  # 背景任務自己抓著，不然會被 GC 掉


async def require_chat_assistant_enabled() -> None:
    """開關沒開＝這組 API 不存在"""
    if not await config_repo.get_config("chat_assistant_enabled", False):
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(require_chat_assistant_enabled)])


# ============================================================================
# 請求模型
# ============================================================================


class HistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class AskRequest(BaseModel):
    kind: Literal["dm", "group"]
    target_id: int = Field(gt=0)
    question: str = Field(min_length=1, max_length=500)
    range: Literal["recent", "unread", "24h", "3d", "message"] = "recent"
    from_id: Optional[int] = Field(
        default=None, ge=0
    )  # 群組 unread：打開前的 last_read+1
    unread_count: Optional[int] = Field(
        default=None, ge=1, le=10000
    )  # 私訊 unread：打開前的未讀數
    around_id: Optional[int] = Field(default=None, gt=0)
    language: Optional[str] = Field(default=None, max_length=10)
    tz: Optional[str] = Field(default=None, max_length=64)
    # 抽屜裡的追問：前端帶前幾輪（從 /history 讀回來的也算），只當上下文
    history: list[HistoryItem] = Field(default_factory=list, max_length=6)

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty question")
        return value

    @model_validator(mode="after")
    def _range_args(self):
        if (
            self.range == "unread"
            and self.from_id is None
            and self.unread_count is None
        ):
            raise ValueError("from_id or unread_count is required for range=unread")
        if self.range == "message" and self.around_id is None:
            raise ValueError("around_id is required for range=message")
        return self


# ============================================================================
# 小工具
# ============================================================================


async def _is_pro(user_id: str) -> bool:
    membership = await run_sync(get_user_membership, user_id)
    return bool(membership.get("is_premium", False))


async def _free_daily_limit() -> int:
    value = await config_repo.get_config(
        "limit_ai_assistant_free_daily", DEFAULT_FREE_DAILY
    )
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_FREE_DAILY


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stage(stage: str, **extra) -> str:
    return _sse({"type": "stage", "stage": stage, **extra})


def _claim_in_flight(user_id: str) -> None:
    now = asyncio.get_running_loop().time()
    started = _IN_FLIGHT.get(user_id)
    if started is not None and now - started < IN_FLIGHT_STALE_SECONDS:
        raise HTTPException(status_code=429, detail="assistant_busy_self")
    _IN_FLIGHT[user_id] = now


async def _model(current_user: dict) -> dict:
    """自帶 key 優先，沒有才用平台模型；都沒有 400 no_model"""
    own = await resolve_user_llm_credentials(current_user)
    if own:
        return {
            "provider": own["provider"],
            "api_key": own["api_key"],
            "model": select_user_llm_model(own),
            "platform": False,
            # 在設定選了平台免費模型（local_llama）也會走到這裡：顯示品牌名，不露內部代號
            "label": PLATFORM_FREE_MODEL_LABEL if is_local_provider(own["provider"]) else own["provider"],
        }
    creds = await run_sync(fallback_credentials, "free")
    if not creds:
        raise HTTPException(status_code=400, detail="no_model")
    return {
        "provider": creds["provider"],
        "api_key": creds.get("api_key") or "",
        "model": creds.get("model"),
        "platform": True,
        "label": PLATFORM_FREE_MODEL_LABEL,
    }


# ============================================================================
# 端點
# ============================================================================


@router.get("/api/chat-assistant/status")
@limiter.limit("60/minute")
async def assistant_status(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """前端決定要不要顯示 ✨、抽屜底部的剩餘次數（開關關＝404）"""
    user_id = current_user["user_id"]
    is_pro = await _is_pro(user_id)
    own = await resolve_user_llm_credentials(current_user)
    limit = None if (is_pro or own) else await _free_daily_limit()
    used = 0 if own else await chat_assistant_repo.used_today(user_id)
    # 平台模型（沒綁 key，或在設定選了免金鑰的 local_llama）：前端寫「使用 CryptoMind Lite」，
    # 不寫「你綁定的 local_llama」（2026-10-01 DANNY：顯示怪怪的）。次數規則照舊看 own
    platform_model = not own or is_local_provider(own["provider"])
    label = PLATFORM_FREE_MODEL_LABEL if platform_model else own["provider"]
    return {
        "own_key": bool(own),
        "platform_model": platform_model,
        "model_provider": None if platform_model else own["provider"],
        "is_pro": is_pro,
        "daily_limit": limit,
        "used_today": used,
        "remaining": None if limit is None else max(0, limit - used),
        "model_label": label,
    }


@router.post("/api/chat-assistant/ask")
@limiter.limit("3/minute;30/hour")
async def ask_assistant(
    request: Request, body: AskRequest, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    _claim_in_flight(user_id)
    handed_off = False
    try:
        prepared = await _prepare(body, current_user)
        _PENDING[user_id] = {
            "kind": body.kind,
            "target_id": body.target_id,
            "question": body.question,
            "started": asyncio.get_running_loop().time(),
        }
        queue = _start_background(body, prepared)
        handed_off = True
        return StreamingResponse(
            _relay(queue),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    finally:
        if not handed_off:
            _IN_FLIGHT.pop(user_id, None)
            _PENDING.pop(user_id, None)


def _start_background(body: AskRequest, prepared: dict) -> asyncio.Queue:
    """產生答案放背景任務：使用者關掉抽屜（斷線）只是不再讀這個 queue，任務照跑、答案照存，
    回來打開從 /history 讀。_stream 的 finally（釋放名額、退次數、清進行中）一定會跑完。"""
    queue: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for event in _stream(body, prepared):
                queue.put_nowait(event)
        except Exception as exc:  # noqa: BLE001 — 背景任務沒人接例外，記 log 並回前端錯誤
            logger.error("[ChatAssistant] stream crashed: %s", exc, exc_info=True)
            queue.put_nowait(_sse({"type": "error", "code": "llm_failed"}))
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(pump())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return queue


async def _relay(queue: asyncio.Queue):
    while True:
        event = await queue.get()
        if event is None:
            return
        yield event


async def _prepare(body: AskRequest, current_user: dict) -> dict:
    """進 SSE 前的檢查（錯誤用 HTTP 狀態碼）：權限 → 模型 → 撈訊息 → 剩餘次數 → 建 client。

    這裡只看還有沒有次數；真的扣在 _stream 開頭——回應沒開始讀就斷線時 generator 根本不會跑，
    finally 也不會跑，在這裡扣就退不回來（review 2026-10-01）。"""
    user_id = current_user["user_id"]
    is_pro = await _is_pro(user_id)
    if body.kind == "group" and not is_pro:
        raise HTTPException(status_code=403, detail="pro_required")
    model = await _model(current_user)
    fetched = await chat_assistant_repo.fetch_messages(
        body.kind,
        body.target_id,
        user_id,
        body.range,
        from_id=body.from_id,
        unread_count=body.unread_count,
        around_id=body.around_id,
    )
    if not fetched.get("success"):
        raise HTTPException(status_code=404, detail="not_found")

    limit = None
    if model["platform"] and not is_pro:
        limit = await _free_daily_limit()
        if await chat_assistant_repo.used_today(user_id) >= limit:
            raise HTTPException(status_code=429, detail="quota_exhausted")
    try:
        client = await run_sync(
            lambda: create_user_llm_client(
                provider=model["provider"],
                api_key=model["api_key"],
                model=model["model"],
                max_tokens=ANSWER_MAX_TOKENS,
            )
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="no_model")
    return {
        "user_id": user_id,
        "messages": fetched["messages"],
        "model": model,
        "client": client,
        "limit": limit,
    }


async def _save_turn(
    body: AskRequest, p: dict, text: str, meta: dict
) -> Optional[dict]:
    """答案存起來（跨裝置接續）。存不起來不影響這次回答：記 log、回 None。
    來源＝這次撈到的文字訊息 id（系統事件不會被收回，不用記）。"""
    source_ids = [
        m["id"] for m in p["messages"] if m.get("type") != "system" and m.get("id")
    ]
    try:
        return await chat_assistant_history_repo.save_turn(
            body.kind,
            body.target_id,
            p["user_id"],
            body.question,
            text,
            source_ids,
            meta,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ChatAssistant] save history failed: %s", exc)
        return None


async def _stream(body: AskRequest, p: dict):
    user_id, model, client = p["user_id"], p["model"], p["client"]
    language = body.language if body.language in LANGUAGE_NAMES else "zh-TW"
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + TOTAL_BUDGET_SECONDS
    acquired = done = counted = False
    remaining = None
    try:
        # 平台模型才記次數（Pro 只記不擋）；滿了（另一個分頁剛用掉）就不回答
        if model["platform"]:
            quota = await chat_assistant_repo.consume_quota(user_id, p["limit"])
            if not quota["ok"]:
                yield _sse({"type": "error", "code": "quota_exhausted"})
                return
            counted = True
            remaining = quota["remaining"]
        yield _stage("reading")
        tz = resolve_tz(body.tz)
        plan = plan_context([format_line(m, tz) for m in p["messages"]], body.range)

        if model["platform"]:
            yield _stage("queued")
            try:
                await asyncio.wait_for(
                    _PLATFORM_SEM.acquire(), timeout=QUEUE_WAIT_SECONDS
                )
            except TimeoutError:
                yield _sse({"type": "error", "code": "busy"})
                return
            acquired = True

        notes: list[str] = []
        truncated = plan["truncated"]
        used = len(plan["lines"])
        if plan["mode"] == "chunks":
            chunks = plan["chunks"]
            yield _stage("condensing", done=0, total=len(chunks))
            condense_deadline = min(deadline, loop.time() + CONDENSE_BUDGET_SECONDS)
            for i, chunk in enumerate(chunks, 1):
                if loop.time() >= condense_deadline:
                    truncated = True  # 來不及濃縮的段（較新的）也算沒讀到
                    break
                notes += await condense_chunks(
                    client, [chunk], language, condense_deadline, lambda _i: None
                )
                used += len(chunk)
                yield _stage("condensing", done=i, total=len(chunks))
            if not notes:
                yield _sse({"type": "error", "code": "llm_failed"})
                return

        yield _stage("thinking")
        text, mode = await answer(
            client,
            body.question,
            trim_history([item.model_dump() for item in body.history]),
            language,
            context_text(plan["lines"], notes, truncated),
            deadline,
        )
        done = True
        logger.info(
            "[ChatAssistant] user=%s kind=%s range=%s msgs=%d mode=%s platform=%s secs=%.1f",
            user_id[:8],
            body.kind,
            body.range,
            len(p["messages"]),
            mode,
            model["platform"],
            loop.time() - started,
        )
        meta = {
            "messages_used": used,
            "truncated": truncated,
            "model": model["label"],
        }
        turn = await _save_turn(body, p, text, meta | {"range": body.range})
        yield _sse(
            {
                "type": "answer",
                "text": text,
                "meta": meta | {"remaining": remaining},
                "turn_id": turn["id"] if turn else None,
            }
        )
    except AssistantFailed as exc:
        logger.warning("[ChatAssistant] user=%s failed (%s)", user_id[:8], exc)
        if loop.time() >= deadline:
            code = "timeout"
        else:
            code = "llm_failed" if model["platform"] else "own_key_failed"
        yield _sse({"type": "error", "code": code})
    finally:
        if acquired:
            _PLATFORM_SEM.release()
        if counted and not done:
            await chat_assistant_repo.refund_quota(user_id)
        _IN_FLIGHT.pop(user_id, None)
        _PENDING.pop(user_id, None)


@router.get("/api/chat-assistant/history")
@limiter.limit("60/minute")
async def assistant_history(
    request: Request,
    kind: Literal["dm", "group"] = Query(...),
    target_id: int = Query(..., gt=0),
    current_user: dict = Depends(get_current_user),
):
    """這個聊天室我問過的（由舊到新），加上「還在整理」的那題（關掉抽屜後回來接著看）。
    聊天室已經看不到（退群、不是自己的對話）→ 404。"""
    user_id = current_user["user_id"]
    result = await chat_assistant_history_repo.list_turns(kind, target_id, user_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail="not_found")
    pending = _PENDING.get(user_id)
    if pending and (pending["kind"], pending["target_id"]) == (kind, target_id):
        elapsed = asyncio.get_running_loop().time() - pending["started"]
        pending = {"question": pending["question"], "elapsed": int(elapsed)}
    else:
        pending = None
    return {"turns": result["turns"], "pending": pending}


@router.delete("/api/chat-assistant/history")
@limiter.limit("30/minute")
async def clear_assistant_history(
    request: Request,
    kind: Literal["dm", "group"] = Query(...),
    target_id: int = Query(..., gt=0),
    current_user: dict = Depends(get_current_user),
):
    """清掉我在這個聊天室的全部問答（只清自己的）"""
    cleared = await chat_assistant_history_repo.clear(
        kind, target_id, current_user["user_id"]
    )
    return {"success": True, "cleared": cleared}


@router.delete("/api/chat-assistant/history/{turn_id}")
@limiter.limit("60/minute")
async def delete_assistant_turn(
    request: Request, turn_id: int, current_user: dict = Depends(get_current_user)
):
    """刪掉其中一則（只能刪自己的；不是自己的當不存在）"""
    if not await chat_assistant_history_repo.delete_turn(
        turn_id, current_user["user_id"]
    ):
        raise HTTPException(status_code=404, detail="not_found")
    return {"success": True}
