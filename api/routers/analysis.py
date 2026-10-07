import asyncio
import collections
import hashlib
import json
import math
import os
import time
import uuid
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.models import QueryRequest
from api.response_metadata import build_response_metadata
from api.user_llm import resolve_user_llm_credentials, select_user_llm_model
from api.utils import logger, run_sync
from core import shared_cache
from core.agents.base_react_agent import empty_response_message
from core.analysis_queue import (
    hitl_resume_window_seconds,
    run_status_ttl_seconds,
    safe_analysis_error_message,
)
from core.audit import audit_log
from core.database import (
    check_session_ownership,
    clip_reasoning,
    create_session,
    delete_session,
    get_chat_history,
    get_current_session,
    get_session_owner,
    get_sessions,
    reorder_pinned_sessions,
    save_chat_message,
    save_codebook_feedback,
    set_current_session,
    toggle_session_pin,
    update_last_active,
)
from core.database import (
    clear_chat_history as db_clear_history,
)
from core.model_config import answer_model_label, get_default_model
from core.onchain.addresses import is_ton_address
from core.orm.analysis_preferences_repo import analysis_preferences_repo
from core.orm.session import get_async_session
from core.validators.content_filter import filter_chat_message
from utils.user_client_factory import create_user_llm_client

router = APIRouter()

# SSE 整體上限：1 小時。深度分析容易跑數分鐘，極端情況更久。
# 必須 >= AGENT_EXECUTION_TIMEOUT（單一 agent 任務上限），否則 SSE 會先斷。
ANALYSIS_TIMEOUT_SECONDS = 3600  # 1 hour

# 客戶端斷線後不取消分析，讓它跑完並存檔（手機切換 App 就會斷線，
# 取消等於使用者每次切出去都白跑）。代價是任務脫離連線後沒人看著，
# 所以閒置監控必須在伺服器端 —— 原本只有前端有（chat-analysis.js 的 600s），
# 客戶端一走就失效。硬上限寬鬆、閒置逾時嚴格，兩者都在伺服器端。
DETACHED_IDLE_TIMEOUT_SECONDS = int(
    # 1800s（30 分鐘）：背景分析的 idle 上限。agent 跑 reasoning model +
    # 多個 tool call 時，LLM 推理階段（無 progress event）可能持續數分鐘，
    # 先前 600s 會在 LLM 推理期間誤判 idle 而殺掉仍在運作的 agent。
    # 對齊 AGENT_EXECUTION_TIMEOUT（1800s）作為合理上限。
    os.getenv("ANALYSIS_IDLE_TIMEOUT_SECONDS", "1800")
)
_DETACHED_IDLE_CHECK_INTERVAL_SECONDS = 15

# asyncio 只持有 task 的弱引用，不留強引用會被 GC 掉
_detached_analysis_tasks: set[asyncio.Task] = set()


# ── 分析執行狀態（run registry）────────────────────────────────────────────
# 客戶端斷線後想知道「跑到哪了」，需要一份伺服器端的輸出快照。
# 本機 dict 是主要來源（同一個 worker 內最快也最準）；shared_cache（Redis）
# 是跨 worker 層 —— WEB_CONCURRENCY > 1 時，狀態查詢可能打到別的 worker，
# 只靠記憶體會查不到。Redis 沒設定時 shared_cache 自動 no-op，退回單 worker 行為。
ANALYSIS_RUN_TTL_SECONDS = hitl_resume_window_seconds()
# 共用快取 key 的 TTL 跟 worker 同一條規則：worker 模式下兩邊寫同一把 key，
# 這裡若只給 900 秒，串流連著時每次同步都把 worker 設的長 TTL 縮回去。
# （續傳時效另由 _load_resume_model_context 以 ANALYSIS_RUN_TTL_SECONDS 判斷；
#  run_status_ttl_seconds 已保證不短於它）
_RUN_CACHE_TTL_SECONDS = run_status_ttl_seconds()
_RUN_SYNC_MIN_INTERVAL_SECONDS = 1.0
_MAX_LOCAL_RUNS = 200
# 可重播的事件筆數上限。超過就只保留尾段，重連時改用 resync（整段內容）補齊。
_MAX_REPLAY_EVENTS = 4000

_local_analysis_runs: "collections.OrderedDict[str, dict]" = collections.OrderedDict()

# run 走到這些狀態就不會再變。waiting＝暫停等使用者回答 HITL，回答走的是
# 另一個新 run，所以對「這個 run」而言同樣是終點。
_SETTLED_RUN_STATUSES = frozenset(
    {"waiting", "completed", "error", "timeout", "cancelled", "revoked"}
)
# 續傳串流的心跳間隔，兼作「本機鏡像是否過期」的檢查點
_RESUME_HEARTBEAT_SECONDS = 20


def _run_cache_key(run_id: str) -> str:
    return f"analysis:run:{run_id}"


def _create_analysis_run(session_id: str, user_id: Optional[str]) -> dict:
    run = {
        "run_id": uuid.uuid4().hex,
        "session_id": session_id,
        "user_id": user_id,
        "status": "running",
        "content": "",
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "next_event_id": 1,
        # 可重播的事件序列（只存在本機；跨 worker 重連改用 content 做 resync）
        "events": [],
        "_last_sync": 0.0,
        # 即時訂閱者的 queue，不進共用快取
        "_subscribers": set(),
        # 信任層：授權撤銷（hackathon 第 6 要素）。revoke endpoint 設 True 後 cancel invoke_task
        "revoked": False,
        # invoke_task 於 event_generator_v4 建立後填入，供 revoke endpoint 跨請求 cancel
        "_invoke_task": None,
    }
    _local_analysis_runs[run["run_id"]] = run
    while len(_local_analysis_runs) > _MAX_LOCAL_RUNS:
        _local_analysis_runs.popitem(last=False)
    _sync_analysis_run(run, force=True)
    return run


def _sync_analysis_run(run: dict, *, force: bool = False) -> None:
    """把狀態寫進共用快取。每個 token 都寫會打爆 Redis，所以做節流。"""
    now = time.monotonic()
    if not force and now - run["_last_sync"] < _RUN_SYNC_MIN_INTERVAL_SECONDS:
        return
    run["_last_sync"] = now
    payload = {k: v for k, v in run.items() if not k.startswith("_") and k != "events"}
    shared_cache.set_json(
        _run_cache_key(run["run_id"]), payload, _RUN_CACHE_TTL_SECONDS
    )


def _finish_analysis_run(
    run: Optional[dict], status: str, *, error: Optional[str] = None
) -> None:
    if not run:
        return
    run["status"] = status
    run["error"] = error
    run["finished_at"] = time.time()
    _sync_analysis_run(run, force=True)


def _reconcile_stale_local_run(run: dict) -> None:
    """worker 模式下本機 run 只是鏡像，靠 _stream_from_redis 轉發事件更新。

    客戶端一斷線（手機切 App），轉發協程跟著被 Starlette 取消，鏡像就永遠
    停在 running——worker 寫進共用快取的狀態才是真的。本機未定案、共用快取
    已定案時，以共用快取回寫本機，否則續傳會掛住、HITL 核准也會被當成過期（409）。

    in-process 模式是本機先改才同步出去，共用快取不會比本機新，這裡不會動到。
    """
    if (run.get("status") or "") in _SETTLED_RUN_STATUSES:
        return
    shared = shared_cache.get_json(_run_cache_key(run["run_id"]))
    if not isinstance(shared, dict):
        return
    if (shared.get("status") or "") not in _SETTLED_RUN_STATUSES:
        return
    run["status"] = shared["status"]
    run["error"] = shared.get("error")
    run["finished_at"] = shared.get("finished_at")
    if shared.get("pending_hitl") is not None:
        run["pending_hitl"] = shared["pending_hitl"]
    if shared.get("content"):
        run["content"] = shared["content"]


def _load_analysis_run(run_id: str) -> Optional[dict]:
    run = _local_analysis_runs.get(run_id)
    if run:
        _reconcile_stale_local_run(run)
        return {k: v for k, v in run.items() if not k.startswith("_") and k != "events"}
    return shared_cache.get_json(_run_cache_key(run_id))


async def _load_resume_model_context(
    body: QueryRequest, current_user: dict
) -> Optional[dict]:
    """Use the paused run's resolved model, not mutable preset/key preferences.

    Older clients may omit the run id. New clients always bind resumes; a missing,
    expired, foreign, or revoked snapshot must never silently switch models.
    """
    if body.resume_run_id is None:
        return None
    if body.resume_answer is None:
        raise HTTPException(
            status_code=400, detail="resume_run_id requires resume_answer"
        )
    run = await run_sync(_load_analysis_run, body.resume_run_id)
    if (
        not run
        or run.get("user_id") != current_user.get("user_id")
        or run.get("session_id") != body.session_id
    ):
        raise HTTPException(status_code=404, detail="Analysis run not found")
    selection = run.get("llm_selection")
    paused_at = run.get("finished_at") or run.get("started_at") or 0
    if (
        run.get("status") != "waiting"
        or run.get("revoked")
        or time.time() - paused_at > ANALYSIS_RUN_TTL_SECONDS
        or not isinstance(selection, dict)
        or not all(
            isinstance(selection.get(key), str) and selection[key]
            for key in ("provider", "model")
        )
    ):
        raise HTTPException(
            status_code=409,
            detail="Analysis resume context expired; start a new analysis",
        )
    if run.get("user_model_preference"):
        from core.database.tools import normalize_membership_tier
        from core.feature_flags import agent_presets_enabled

        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if not agent_presets_enabled() or tier != "premium":
            raise HTTPException(
                status_code=409,
                detail="Model preset is no longer available; start a new analysis",
            )
    return run


def _json_safe(value):
    """SSE 邊界防護：遞迴把 NaN/Infinity 清為 None。

    ``json.dumps`` 預設 allow_nan=True 會輸出 ``NaN`` token，瀏覽器
    ``JSON.parse`` 不認得會直接 throw——前端把 parse 失敗的幀靜默丟棄。
    2026-09-21 線上案例：hitl_question 帶 NaN 匯率 → 同意卡整張消失，
    使用者只看到「請在下方輸入回應」空 fallback 卡。數字欄位的顯示端
    本來就把 None 當「查不到」，清成 None 是最小破壞的收斂。
    """
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _emit_run_event(run: Optional[dict], payload: dict) -> str:
    """記錄一筆事件並回傳 SSE 字串（含 id，供斷線重連時續傳）。

    run 可能是 None —— 例外有機會在 run 建立之前就發生，那時仍要能把錯誤送出去，
    不可以讓這裡的 NameError 蓋掉原始錯誤。
    """
    payload = _json_safe(payload)
    if run is None:
        return f"data: {json.dumps(payload)}\n\n"

    event_id = run["next_event_id"]
    run["next_event_id"] = event_id + 1

    frame = f"id: {event_id}\ndata: {json.dumps(payload)}\n\n"

    run["events"].append({"id": event_id, "frame": frame})
    if len(run["events"]) > _MAX_REPLAY_EVENTS:
        del run["events"][: len(run["events"]) - _MAX_REPLAY_EVENTS]

    # 即時扇出給已重連的訂閱者
    for queue in tuple(run["_subscribers"]):
        try:
            queue.put_nowait(frame)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            run["_subscribers"].discard(queue)

    return frame


def _pause_run_for_hitl(run: Optional[dict], question: Any) -> str:
    """HITL 暫停：記下待回答的問題、標成 waiting，回傳 hitl_question 幀。

    問題必須跟著 run 存（含共用快取）：斷線重連時事件緩衝可能蓋不到斷點、
    重連可能落在別的 worker，或客戶端收過這一幀但畫面已被斷線提示蓋掉——
    續傳路徑都靠這份存檔重送同意卡。
    """
    if run is not None:
        run["pending_hitl"] = _json_safe(question)
    _finish_analysis_run(run, "waiting")
    return _emit_run_event(run, {"type": "hitl_question", "data": question})


def _is_hitl_question_frame(frame: str) -> bool:
    # 幀由 _emit_run_event 以預設 json.dumps 產生；內容裡的引號會被跳脫，
    # 未跳脫的這個片段只可能是頂層 type 欄位
    return '"type": "hitl_question"' in frame


def _sse_data(payload: dict) -> str:
    return f"data: {json.dumps(_json_safe(payload))}\n\n"


def _resume_end_frames(run: dict, *, hitl_sent: bool) -> list[str]:
    """續傳串流的收尾幀。

    waiting：補送待回答的問題（這條續傳還沒送過才送），再送 done+waiting
    讓前端進入暫停狀態——不是 loadChatHistory，問題不在對話紀錄裡。
    """
    status = run.get("status") or ""
    if status != "waiting":
        return [_sse_data({"done": True, "status": status, "error": run.get("error")})]
    frames = []
    pending = run.get("pending_hitl")
    if pending is not None and not hitl_sent:
        frames.append(_sse_data({"type": "hitl_question", "data": pending}))
    frames.append(_sse_data({"done": True, "waiting": True, "status": "waiting"}))
    return frames


def _replay_run_events(run: dict, after_id: int) -> tuple[list[str], bool]:
    """回傳 (要重播的 frame, 是否需要 resync)。

    要求的位置比緩衝區起點更舊時無法逐筆重播，改回報 resync ——
    由呼叫端送出目前累積的完整內容，避免中間漏掉一段。
    """
    events = run.get("events") or []
    if not events:
        return [], after_id > 0
    if after_id and after_id < events[0]["id"] - 1:
        return [], True
    return [e["frame"] for e in events if e["id"] > after_id], False


def _spawn_detached(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _detached_analysis_tasks.add(task)
    task.add_done_callback(_detached_analysis_tasks.discard)
    return task


async def _finish_analysis_detached(
    invoke_task: asyncio.Task,
    last_activity: dict,
    *,
    run: Optional[dict] = None,
    manager: Any,
    session_id: str,
    user_id: Optional[str],
    language: str,
) -> None:
    """客戶端斷線後，把分析跑完並把結果存進 DB。

    存檔走 core.database.save_chat_message（自己開連線）與 run_sync 的執行緒池，
    兩者都不依賴請求的 AsyncSession，所以請求結束後仍可安全寫入。
    """
    try:
        while True:
            done, _ = await asyncio.wait(
                [invoke_task], timeout=_DETACHED_IDLE_CHECK_INTERVAL_SECONDS
            )
            if done:
                break

            idle_for = time.monotonic() - last_activity["at"]
            if idle_for > DETACHED_IDLE_TIMEOUT_SECONDS:
                logger.warning(
                    "[V4] 背景分析閒置 %.0fs 超過上限，中止：session=%s",
                    idle_for,
                    session_id,
                )
                invoke_task.cancel()
                break

        result = await invoke_task

        # HITL 中斷：沒有 final_response、不存檔，但問題要留著並推給已重連的
        # 訂閱者——舊碼標成 completed，使用者切回來同意卡就永遠消失了
        interrupt_events = (
            result.get("__interrupt__") if isinstance(result, dict) else None
        )
        if interrupt_events:
            _pause_run_for_hitl(run, getattr(interrupt_events[0], "value", None))
            _emit_run_event(run, {"done": True, "waiting": True})
            logger.info("[V4] 背景分析暫停等待 HITL 回答：session=%s", session_id)
            return

        response = result.get("final_response") if isinstance(result, dict) else None
        if not response:
            logger.info("[V4] 背景分析無最終回應，不存檔：session=%s", session_id)
            _finish_analysis_run(run, "completed")
            return

        await run_sync(
            lambda: save_chat_message(
                "assistant",
                response,
                session_id=session_id,
                user_id=user_id,
            )
        )
        if run is not None:
            run["content"] = response
        _finish_analysis_run(run, "completed")
        logger.info("[V4] 背景分析完成並已存檔：session=%s", session_id)
    except asyncio.CancelledError:
        logger.info("[V4] 背景分析被取消：session=%s", session_id)
        _finish_analysis_run(run, "cancelled", error="idle_timeout")
    except asyncio.TimeoutError:
        logger.warning("[V4] 背景分析達硬上限逾時：session=%s", session_id)
        _finish_analysis_run(run, "timeout", error="analysis_timeout")
    except Exception:
        logger.exception("[V4] 背景分析失敗：session=%s", session_id)
        _finish_analysis_run(run, "error", error="internal_error")
    finally:
        if getattr(manager, "progress_callback", None) is not None:
            manager.progress_callback = None


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None


# --- Session Management Endpoints ---


@router.get("/api/chat/sessions")
async def get_user_sessions(
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """獲取用戶對話列表"""
    user_id = current_user["user_id"]

    # 訪客追蹤：開 chat 頁時更新 last_active + 記 audit_log（後台 Visitors 分頁資料來源）。
    # audit_log 是 sync 但內部 fire-and-forget async（不拋例外）；update_last_active 是
    # sync DB I/O，需走 run_sync 避免阻塞 event loop。
    try:
        await run_sync(update_last_active, user_id)
    except Exception:
        pass  # 統計用，失敗不擋主流程
    audit_log(
        action="chat_page_visited",
        user_id=user_id,
        username=current_user.get("username"),
    )

    sessions = await run_sync(
        lambda: get_sessions(user_id=user_id, limit=limit, offset=offset)
    )
    return {"success": True, "sessions": sessions}


@router.post("/api/chat/sessions")
@limiter.limit("20/minute")
async def create_user_session(
    request: Request,
    body: Optional[CreateSessionRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """Create a new chat session for the current user.

    503 而非 500：DB 還在 init / pool 暫時滿時，用戶端只要稍候再試就能成功，
    屬暫時性不可用，不是伺服器 bug。
    """
    user_id = current_user["user_id"]
    session_id = str(uuid.uuid4())
    title = (body.title.strip() if body and body.title else "") or "New Chat Session"

    try:
        await run_sync(lambda: create_session(session_id, title=title, user_id=user_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except RuntimeError as e:
        # wait_for_db_ready_sync timeout → "Database initialization is still in progress"
        if "Database initialization" in str(e):
            raise HTTPException(status_code=503, detail="database_initializing")
        raise HTTPException(status_code=503, detail="database_unavailable")
    except Exception as e:
        logger.error("create_user_session failed: %s", e)
        raise HTTPException(status_code=503, detail="database_unavailable")
    # fire-and-forget 整合舊 session（新對話情境：lazy creation 時觸發）
    old_session_id = await run_sync(get_current_session, user_id)
    if old_session_id and old_session_id != session_id:
        _trigger_session_consolidation(user_id, old_session_id)
    return {"success": True, "session_id": session_id, "title": title}


def _trigger_session_consolidation(user_id: str, old_session_id: str) -> None:
    """fire-and-forget：整合舊 session 記憶後 invalidate 該 session manager。

    換對話（切換既有對話 / 開新對話）時呼叫。用 _run_background（repo 既有
    pattern）確保不阻塞 API 回應、例外被 _on_done 記錄、reference 不被 GC。

    防護：
    - _background_memory_consolidation 內部有 _consolidation_lock +
      _consolidating flag，重複觸發會直接 return False（不會重複整合）
    - 用 _message_count <= 0 早退，避免對沒新訊息的 session 白跑
    - invalidate 放在 finally：無論整合成功失敗都釋放 manager，避免殭屍
    """
    from core.agents.bootstrap import (
        get_manager_instance,
        invalidate_manager_cache,
    )
    from core.agents.manager._main import _run_background

    old_manager = get_manager_instance(user_id, old_session_id)
    if not old_manager or getattr(old_manager, "_message_count", 0) <= 0:
        return

    async def _consolidate_then_invalidate():
        try:
            await old_manager._background_memory_consolidation()
        finally:
            invalidate_manager_cache(user_id, old_session_id)

    _run_background(_consolidate_then_invalidate())


class CurrentSessionRequest(BaseModel):
    session_id: str


@router.get("/api/chat/current-session")
async def get_current_session_endpoint(
    current_user: dict = Depends(get_current_user),
):
    """跨平台共用的「當前對話」—— 網頁載入時讀它，接續 Telegram/他處最後的對話。"""
    sid = await run_sync(get_current_session, current_user["user_id"])
    return {"success": True, "session_id": sid}


@router.post("/api/chat/current-session")
@limiter.limit("60/minute")
async def set_current_session_endpoint(
    request: Request,
    body: CurrentSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    """網頁切換對話時寫回共用指標 → Telegram 端隨之接續同一對話。

    同時觸發舊 session 的背景記憶整合（fire-and-forget，不阻塞回應）。
    """
    user_id = current_user["user_id"]
    owns = await run_sync(check_session_ownership, body.session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403, detail="Forbidden: session does not belong to you"
        )
    # 先讀舊 session_id（寫入新 id 之前），用於背景整合
    old_session_id = await run_sync(get_current_session, user_id)
    await run_sync(set_current_session, user_id, body.session_id)
    # fire-and-forget 整合舊 session（切換既有對話情境）
    if old_session_id and old_session_id != body.session_id:
        _trigger_session_consolidation(user_id, old_session_id)
    return {"success": True}


class SessionPinOrderRequest(BaseModel):
    session_ids: list[Annotated[str, Field(max_length=128)]] = Field(
        ..., max_length=100
    )


# 要在 /api/chat/sessions/{session_id} 系列前面註冊（pin-order 不能被當成 session_id）
@router.put("/api/chat/sessions/pin-order")
@limiter.limit("30/minute")
async def reorder_pinned_sessions_endpoint(
    request: Request,
    body: SessionPinOrderRequest,
    current_user: dict = Depends(get_current_user),
):
    """拖曳排序置頂對話（寬鬆）：只動自己目前置頂的，別人的／沒置頂的 id 直接略過"""
    await run_sync(reorder_pinned_sessions, current_user["user_id"], body.session_ids)
    return {"success": True}


@router.delete("/api/chat/sessions/{session_id}")
@limiter.limit("20/minute")
async def delete_user_session(
    request: Request, session_id: str, current_user: dict = Depends(get_current_user)
):
    """刪除特定對話"""
    user_id = current_user["user_id"]
    owns = await run_sync(check_session_ownership, session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403, detail="Forbidden: session does not belong to you"
        )
    await run_sync(delete_session, session_id)
    return {"status": "success", "message": f"Session {session_id} deleted"}


@router.put("/api/chat/sessions/{session_id}/pin")
@limiter.limit("30/minute")
async def pin_user_session(
    request: Request,
    session_id: str,
    is_pinned: bool = Query(..., description="Set to true to pin, false to unpin"),
    current_user: dict = Depends(get_current_user),
):
    """切換對話置頂狀態"""
    user_id = current_user["user_id"]
    owns = await run_sync(check_session_ownership, session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403, detail="Forbidden: session does not belong to you"
        )
    await run_sync(lambda: toggle_session_pin(session_id, is_pinned))
    return {"status": "success", "session_id": session_id, "is_pinned": is_pinned}


# ============================================================================
# Chat 招呼語 — Trustworthy AI Hackathon（Principal：Agent 主動表明身份）
#
# 進入聊天頁時，用小模型生成一句個人化招呼（例如「您好鈺澤，我是
# CryptoMind 小幫手，今天有什麼事？」），讓評審一進頁面就看到可信 Agent
# 知道自己在跟誰說話、且主動揭露身份。
#
# 快取：以 (user_id, session_id, language) 為 key；同 session 重整不重扣額度。
# 失敗容忍：LLM 掛掉時 fallback 一句靜態招呼，不讓使用者看到 500。
# ============================================================================

# In-process greeting 快取（session 生命週期；服務重啟自動清空）
_GREETING_CACHE: dict = {}
_GREETING_CACHE_TTL_SECONDS = 3600  # 1 小時後強制重生成
# 全局 entry 上限：防止惡意/大量 session_id 累積導致記憶體漏（長跑 worker 數天不重啟）。
# 寫入時若超過上限會先掃掉所有過期 entry；仍超過則不寫入（cache miss 退化為重生成，不影響正確性）。
_GREETING_CACHE_MAX_ENTRIES = 5000

# User-level 短窗快取（#7 成本 DoS 防護）。
# 即使 session-level cache miss（例如惡意使用者循環 session_id），同一 user 在此短窗內
# 仍重用最近一次成功的 greeting，避免每次都重扣 server-paid LLM key。
# 5 分鐘夠短：正常使用者開新 session 拿到「上一句」無感；攻擊者燒額度被大幅壓制。
# key = (user_id, language)；value = {text, ts, fallback}。
_GREETING_USER_RECENT: dict = {}
_GREETING_USER_RECENT_TTL_SECONDS = 300  # 5 分鐘


def _greeting_cache_key(user_id: str, session_id: str, language: str) -> str:
    return f"{user_id}:{session_id}:{language}"


def _sweep_greeting_cache(now: float) -> None:
    """清掉所有過期 entry。寫入前呼叫，避免 cache 無上限生長。"""
    if _GREETING_CACHE:
        expired = [
            k
            for k, v in _GREETING_CACHE.items()
            if (now - v.get("ts", 0)) >= _GREETING_CACHE_TTL_SECONDS
        ]
        for k in expired:
            _GREETING_CACHE.pop(k, None)
    if _GREETING_USER_RECENT:
        expired_u = [
            k
            for k, v in _GREETING_USER_RECENT.items()
            if (now - v.get("ts", 0)) >= _GREETING_USER_RECENT_TTL_SECONDS
        ]
        for k in expired_u:
            _GREETING_USER_RECENT.pop(k, None)


def invalidate_greeting_cache(user_id: str) -> int:
    """清除指定使用者所有 greeting 快取(跨 session/language/user-recent)。

    改名後必須呼叫,否則舊 greeting(含舊暱稱)會被快取命中持續回傳 1 小時。
    回傳清除的 entry 數。
    """
    if not user_id:
        return 0
    prefix = f"{user_id}:"
    stale = [k for k in _GREETING_CACHE if k.startswith(prefix)]
    for k in stale:
        _GREETING_CACHE.pop(k, None)
    # user-recent cache 以 (user_id, language) tuple 為 key，清掉該 user 所有 language
    stale_u = [
        k for k in _GREETING_USER_RECENT if isinstance(k, tuple) and k[0] == user_id
    ]
    for k in stale_u:
        _GREETING_USER_RECENT.pop(k, None)
    return len(stale) + len(stale_u)


async def _resolve_principal_wallets(user_id) -> tuple[Optional[str], Optional[str]]:
    """(舊 TON 身份錢包, 已驗證 EVM 地址)——Agent principal 與招呼「在跟誰對話」用。

    TON 登入已拔除：舊 TON 帳號 user_id 即錢包，維持原樣；其他人（evm_／tg_／g_）
    查 get_verified_evm_address（user_wallets → evm_ 身份 → users.evm_address）。
    查詢失敗就當沒錢包，不擋聊天。
    """
    if not isinstance(user_id, str) or not user_id:
        return None, None
    if is_ton_address(user_id):
        return user_id, None
    try:
        from core.database.user import get_verified_evm_address

        return None, await run_sync(get_verified_evm_address, user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[principal] EVM wallet lookup failed for %s: %s", user_id, exc)
        return None, None


def _fallback_greeting(display_name: str, language: str) -> str:
    """LLM 掛掉時的靜態 fallback。溫暖、帶名稱、與前端 i18n 文案一致。"""
    name = display_name or ""
    if language == "en":
        if name:
            return f"{name}, welcome back. I'm your CryptoMind assistant, here to help you navigate the markets anytime. What's on your mind today?"
        return "Welcome. I'm your CryptoMind assistant, here to help you navigate the markets anytime. What would you like to explore?"
    if language == "zh-CN":
        if name:
            return f"{name}，欢迎回来～我是你的 CryptoMind 小帮手，随时陪你掌握市场脉动。今天想聊聊什么？"
        return "欢迎你～我是 CryptoMind 小帮手，随时陪你掌握市场脉动。今天想了解什么？"
    # zh-TW / 其他預設 zh-TW
    if name:
        return f"{name}，歡迎回來～我是你的 CryptoMind 小幫手，隨時陪你掌握市場脈動。今天想聊聊什麼？"
    return "歡迎你～我是 CryptoMind 小幫手，隨時陪你掌握市場脈動。今天想了解什麼？"


async def _generate_greeting(
    *,
    display_name: Optional[str],
    wallet_address: Optional[str],
    language: str,
    tier: str,
    current_user: Optional[dict] = None,
) -> str:
    """用使用者 BYOK 的小模型生成一句個人化招呼。

    與 /api/analyze 一致走 BYOK（resolve_user_llm_credentials + create_user_llm_client），
    而非 server-side key。沒有 BYOK 或 LLM 呼叫失敗時 raise；
    呼叫端負責 fallback 到 _fallback_greeting()。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # 走使用者 BYOK（與 chat 分析一致）。沒 BYOK → raise 由 endpoint fallback。
    credentials = await resolve_user_llm_credentials(current_user)
    if not credentials:
        from core.agents.fallback import fallback_credentials

        credentials = fallback_credentials()
    if not credentials:
        raise RuntimeError("no BYOK LLM api key available for greeting")

    llm = create_user_llm_client(
        provider=credentials["provider"],
        api_key=credentials["api_key"],
        model=credentials["model"],
        max_tokens=80,
    )

    # 脫敏：錢包地址只留前綴 + 末 4 碼，符合 AGENTS.md「不得把真實值放進 prompt」。
    # 鏈中立：wallet_address 可能是舊 TON 身份，也可能是 EVM 地址。
    wallet_hint = ""
    if wallet_address and len(wallet_address) >= 8:
        wallet_hint = f"（已驗證錢包 {wallet_address[:4]}...{wallet_address[-4:]}）"

    name_hint = display_name or "使用者"

    if language == "en":
        sys_msg = (
            "You are CryptoMind, a trustworthy AI assistant for crypto/finance. "
            "Generate ONE short, warm greeting (<=30 words) to a verified user. "
            "Introduce yourself briefly, address them by name, and ask how you can help. "
            "Do NOT reveal the wallet address. No markdown, no tools."
        )
        user_msg = (
            f"User: {name_hint} {wallet_hint}, tier: {tier}. "
            "Greet them now in one sentence."
        )
    elif language == "zh-CN":
        sys_msg = (
            "你是 CryptoMind，一个可信的加密与金融 AI 助手。"
            "请生成一句简短温暖的招呼（不超过 30 字）：简短自我介绍、用名字称呼对方、"
            "并询问今天能协助什么。不可透露钱包地址。不要 markdown、不要调用工具。"
        )
        user_msg = (
            f"使用者：{name_hint} {wallet_hint}，会员等级：{tier}。请用一句话招呼。"
        )
    else:
        sys_msg = (
            "你是 CryptoMind，一個可信的加密與金融 AI 助手。"
            "請生成一句簡短溫暖的招呼（不超過 30 字）：簡短自我介紹、用名字稱呼對方、"
            "並詢問今天能協助什麼。不可透露錢包地址。不要 markdown、不要呼叫工具。"
        )
        user_msg = (
            f"使用者：{name_hint} {wallet_hint}，會員等級：{tier}。請用一句話招呼。"
        )

    response = await llm.ainvoke([SystemMessage(sys_msg), HumanMessage(user_msg)])
    text = getattr(response, "content", "") or ""
    text = text.strip().strip('"').strip("'")
    return text or _fallback_greeting(name_hint, language)


class GreetingRequest(BaseModel):
    session_id: str
    language: str = "zh-TW"


@router.post("/api/chat/greeting")
@limiter.limit("3/minute")
async def get_chat_greeting(
    request: Request,
    body: GreetingRequest,
    current_user: dict = Depends(get_current_user),
):
    """生成個人化聊天招呼語（session 快取、LLM 失敗 fallback）。"""
    import time

    user_id = current_user["user_id"]
    language = body.language or "zh-TW"
    session_id = body.session_id or "default"

    cache_key = _greeting_cache_key(user_id, session_id, language)
    now = time.time()

    cached = _GREETING_CACHE.get(cache_key)
    if cached and (now - cached["ts"]) < _GREETING_CACHE_TTL_SECONDS:
        return {
            "success": True,
            "greeting": cached["text"],
            "cached": True,
            "fallback": cached.get("fallback", False),
        }

    # #7 成本 DoS 防護：session cache miss 時，先查 user-level 短窗 cache。
    # 壓制「循環 session_id 燒 server LLM key」——同 user 5 min 內重用最近一次結果。
    user_recent_key = (user_id, language)
    user_recent = _GREETING_USER_RECENT.get(user_recent_key)
    if user_recent and (now - user_recent["ts"]) < _GREETING_USER_RECENT_TTL_SECONDS:
        # 同時回填 session cache，後續同 session 直接命中
        _sweep_greeting_cache(now)
        if len(_GREETING_CACHE) < _GREETING_CACHE_MAX_ENTRIES:
            _GREETING_CACHE[cache_key] = {
                "text": user_recent["text"],
                "ts": now,
                "fallback": user_recent.get("fallback", False),
            }
        return {
            "success": True,
            "greeting": user_recent["text"],
            "cached": True,
            "fallback": user_recent.get("fallback", False),
        }

    # 從 DB 讀 display_name（NULL → fallback username）
    from core.database.user import get_user_display_name

    display_name = await run_sync(get_user_display_name, user_id)
    if not display_name:
        display_name = current_user.get("username")

    ton_wallet, evm_address = await _resolve_principal_wallets(user_id)
    wallet_address = ton_wallet or evm_address

    tier = current_user.get("membership_tier", "free")

    try:
        greeting_text = await _generate_greeting(
            display_name=display_name,
            wallet_address=wallet_address,
            language=language,
            tier=tier,
            current_user=current_user,
        )
        is_fallback = False
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[chat/greeting] LLM failed, using fallback: {e}")
        greeting_text = _fallback_greeting(display_name, language)
        is_fallback = True

    # 寫入前清過期 entry；若清完仍超上限則不寫（退化為下次重生成，不影響正確性）。
    _sweep_greeting_cache(now)
    if len(_GREETING_CACHE) < _GREETING_CACHE_MAX_ENTRIES:
        _GREETING_CACHE[cache_key] = {
            "text": greeting_text,
            "ts": now,
            "fallback": is_fallback,
        }
    # 同時寫入 user-level 短窗 cache（跨 session 重用）
    _GREETING_USER_RECENT[user_recent_key] = {
        "text": greeting_text,
        "ts": now,
        "fallback": is_fallback,
    }

    return {
        "success": True,
        "greeting": greeting_text,
        "cached": False,
        "fallback": is_fallback,
    }


@limiter.limit("30/minute")
@router.get("/api/chat/history")
async def get_history(
    request: Request,
    session_id: str,
    before_timestamp: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """獲取對話歷史（支援動態載入）。

    - 初始載入：不傳 before_timestamp，回傳最新 20 條
    - 向上捲動載入：傳入 before_timestamp（最舊可見訊息的時間），回傳更早的 20 條
    - has_more=True 表示還有更舊的訊息可載入
    """
    user_id = current_user["user_id"]
    owns = await run_sync(check_session_ownership, session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403, detail="Forbidden: session does not belong to you"
        )
    LIMIT = 20
    history = await run_sync(
        lambda: get_chat_history(
            session_id=session_id, limit=LIMIT + 1, before_timestamp=before_timestamp
        )
    )
    has_more = len(history) > LIMIT
    if has_more:
        history = history[1:]
    return {"success": True, "history": history, "has_more": has_more}


async def _stream_from_redis(run_id: str, run: Optional[dict]):
    """從 Redis pub/sub 訂閱 worker 發出的事件，轉成 SSE frames。

    worker 模式：分析跑在獨立進程，API 端只負責轉發事件。
    跟 event_generator_v4 的 yield 格式一致（前端零改動）。
    """
    from core.analysis_queue import subscribe_events

    accumulated_content = ""

    async for event in subscribe_events(run_id, timeout=15):
        # keep-alive（timeout 無事件）
        if event is None:
            yield ": keep-alive\n\n"
            continue

        event_type = event.get("type")

        if event_type == "run_started":
            if run:
                yield _emit_run_event(run, {"type": "run_started", "run_id": run_id})

        elif event_type == "progress":
            data = event.get("data", {})
            # token 事件：與 in-process event_generator_v4 對齊，改發 content 幀。
            # 舊版把 token 原封以 progress 幀轉發，前端 applyProgress 對 token
            # 型別直接 return —— worker 模式下合成階段完全沒有串流文字，
            # 使用者只看到 "synthesizing" 卡轉圈（2026-08-25 線上回報）。
            if data.get("type") == "token":
                chunk = data.get("data", {}).get("chunk", "")
                if chunk:
                    accumulated_content += chunk
                    if run:
                        run["content"] = accumulated_content
                        _sync_analysis_run(run)
                    yield _emit_run_event(run, {"content": chunk, "type": "token"})
                else:
                    yield _emit_run_event(run, {"type": "progress", "data": data})
            elif data.get("type") == "reasoning":
                # 思考內容獨立一種幀：不能併進 run["content"]（那是答案本身），
                # 前端拿去渲染可摺疊區塊。
                chunk = data.get("data", {}).get("chunk", "")
                if chunk:
                    yield _emit_run_event(run, {"content": chunk, "type": "reasoning"})
            else:
                yield _emit_run_event(run, {"type": "progress", "data": data})

        elif event_type == "hitl_question":
            yield _pause_run_for_hitl(run, event.get("data"))

        elif event_type == "final":
            accumulated_content = event.get("content", "")
            yield _emit_run_event(
                run, {"content": accumulated_content, "type": "final"}
            )

        elif event_type == "response_metadata":
            yield _emit_run_event(
                run, {"type": "response_metadata", "data": event.get("data", {})}
            )

        elif event_type == "revoked":
            if run:
                _finish_analysis_run(run, "revoked")
            yield _emit_run_event(
                run,
                {
                    "type": "revoked",
                    "message": event.get(
                        "message", "Authorization revoked; the agent has been stopped."
                    ),
                    "done": True,
                },
            )
            # 撤銷幀自帶 done：不 break 會落進下面的 done 分支被改記成 completed
            break

        elif event_type == "error" or (event_type is None and event.get("error")):
            # 舊版 worker 的錯誤／超時幀沒有 type（{"error": 原始例外, "done": True}），
            # 部署交接期間仍可能收到：一樣當錯誤，且原始字串先過濾再給使用者
            message = event.get("error") or "Analysis failed"
            if event_type is None:
                message = safe_analysis_error_message(message)
            if run:
                _finish_analysis_run(run, "error", error=message)
            yield _emit_run_event(run, {"error": message, "done": True})
            break

        # done / done with waiting
        if event.get("done"):
            if run and event.get("waiting"):
                _finish_analysis_run(run, "waiting")
                yield _emit_run_event(run, {"done": True, "waiting": True})
            else:
                if run:
                    run["content"] = accumulated_content
                    _finish_analysis_run(run, "completed")
                yield _emit_run_event(run, {"done": True})
            break


def _build_job_envelope(
    run_id: str,
    session_id: str,
    user_id: str,
    credentials: dict,
    graph_input,
    config: dict,
    *,
    language: str = "zh-TW",
    user_tier: str = "free",
    display_name: Optional[str] = None,
    wallet_address: Optional[str] = None,
    evm_address: Optional[str] = None,
    web_mode: str = "web",
    key_fingerprint: str = "",
    resume_answer=None,
    llm_selection: Optional[dict] = None,
    user_model_preference: Optional[str] = None,
    router_selection: Optional[dict] = None,
) -> dict:
    """把分析任務的所有參數打包成 JSON-serializable 的 job envelope。"""
    from langgraph.types import Command

    # graph_input 是 Command 物件，要序列化
    if isinstance(graph_input, Command):
        if graph_input.resume is not None:
            # update 一併帶走：resume 輪若帶了 state 更新，worker 重建 Command 要保留
            gi = {"resume": graph_input.resume, "update": graph_input.update or {}}
        else:
            gi = {
                "goto": graph_input.goto,
                "update": graph_input.update,
            }
    else:
        gi = {"goto": "claw_loop", "update": {}}

    return {
        "run_id": run_id,
        "session_id": session_id,
        "user_id": user_id,
        "language": language,
        "user_tier": user_tier,
        "display_name": display_name,
        "wallet_address": wallet_address,
        "evm_address": evm_address,
        "credentials": credentials,
        "key_fingerprint": key_fingerprint,
        "web_mode": web_mode,
        "graph_input": gi,
        "config": config,
        "resume_answer": resume_answer,
        "llm_selection": llm_selection,
        "user_model_preference": user_model_preference,
        # Router 模型（worker 端重建 client 用）：transient——只走 Redis
        # stream，與 credentials.api_key 同一信任邊界；絕不持久化。
        "router_selection": router_selection,
        "created_at": time.time(),
    }


# --- Analysis Endpoint ---


class PlatformModelStatus(BaseModel):
    """沒綁金鑰時平台提供的模型狀態（前端解鎖輸入框、顯示品牌名與剩餘次數用）。"""

    available: bool
    label: str
    tier: str
    daily_limit: Optional[int] = None  # None = 不限
    used_today: int = 0
    remaining: Optional[int] = None  # None = 不限
    local: bool = False


@router.get("/api/platform-model", response_model=PlatformModelStatus)
@limiter.limit("60/minute")
async def platform_model_status(
    request: Request, current_user: dict = Depends(get_current_user)
) -> PlatformModelStatus:
    """登入使用者沒綁 API key 時，平台會不會提供模型、叫什麼、今天還剩幾次。

    2026-09-22 DANNY：所有等級都走本地模型；免費會員每日上限是 FREE_DAILY_CHAT_LIMIT
    （core/config.py 預設 5，env 或後台設定中心可調；這裡原本寫的「20/天」與預設不符，2026-10-05 更正）、
    進階會員不限。若有雲端備援且正在用（本地掛了），才套 BYOK_FALLBACK_DAILY_LIMIT。
    對外只露平台品牌名，不露底層模型。
    """
    from core.agents.fallback import fallback_credentials, fallback_daily_limit
    from core.config import FREE_DAILY_CHAT_LIMIT
    from core.database.tools import normalize_membership_tier
    from core.model_config import PLATFORM_FREE_MODEL_LABEL

    tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
    creds = await run_sync(lambda: fallback_credentials(tier=tier))
    if not creds:
        return PlatformModelStatus(
            available=False, label=PLATFORM_FREE_MODEL_LABEL, tier=tier
        )
    try:
        used = await run_sync(
            lambda: _count_chat_messages_today(current_user["user_id"])
        )
    except Exception:  # noqa: BLE001 — 計數失敗不擋（與 analyze 同一降級）
        used = 0
    limit: Optional[int]
    if tier != "premium":
        from core.setting_overrides import int_param  # 後台覆寫 > env

        limit = int_param("FREE_DAILY_CHAT_LIMIT", FREE_DAILY_CHAT_LIMIT)
    elif not creds.get("local"):
        limit = fallback_daily_limit()
    else:
        limit = None
    return PlatformModelStatus(
        available=True,
        label=PLATFORM_FREE_MODEL_LABEL,
        tier=tier,
        daily_limit=limit,
        used_today=used,
        remaining=(max(0, limit - used) if limit is not None else None),
        local=bool(creds.get("local")),
    )


def _count_chat_messages_today(user_id: str) -> int:
    """數使用者今天發了幾則 AI 聊天訊息（免費每日上限用）。

    用 conversation_history 的 user role（每次 analyze 寫一筆 user prompt），
    有 (user_id, timestamp DESC) index。sync（psycopg2），呼叫端包 run_sync。
    """
    from core.database.connection import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT COUNT(*) FROM conversation_history
                WHERE user_id = %s AND role = 'user' AND DATE(timestamp) = CURRENT_DATE
                """,
                (user_id,),
            )
            return c.fetchone()[0]
    finally:
        conn.close()


async def _resolve_preset_model_override(
    session: AsyncSession,
    current_user: dict,
    preset_id: Optional[str],
) -> Optional[dict]:
    """Model Mixer Step 1：單 agent preset 的 per-agent 模型指定。

    - preset 恰好選一個 agent、且該 agent 在 user_agent_configs 有模型指定
      → 回傳 {"agent_id", "provider", "model", "preset_id"}。
    - 多 agent preset → None：整個 run 用使用者預設模型。
    - **非 Premium → None**：寫入端是 Premium-only，讀取端也必須重驗，否則
      使用者降級後那列 user_agent_configs 還在，會繼續享有 Premium 功能
      （比照隔壁 _resolve_agent_preset_config 把 tier 傳進 resolver）。
    - 任何失敗 → None（降級為使用者預設模型，不阻斷聊天）。

    這裡只做「要不要換模型」的早期解析（在 create_user_llm_client 之前）；
    工具池解析仍由 _resolve_agent_preset_config 在原位負責。
    """
    try:
        from core.database.tools import normalize_membership_tier
        from core.feature_flags import agent_presets_enabled
        from core.orm.agent_configs_repo import agent_configs_repo
        from core.orm.agent_presets_repo import agent_presets_repo

        if not agent_presets_enabled():
            return None

        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if tier != "premium":
            return None

        user_id = current_user.get("user_id")
        preset = None
        if preset_id:
            preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
        if preset is None:
            preset = await agent_presets_repo.get_default_preset(session, user_id)
        if preset is None:
            return None

        agent_ids = list(preset.agent_ids or [])
        if len(agent_ids) != 1:
            return None

        config = await agent_configs_repo.get_config(
            session, preset.preset_id, agent_ids[0]
        )
        selection = config.model_selection if config else None
        if not isinstance(selection, dict):
            return None
        provider = str(selection.get("provider") or "").strip()
        model = str(selection.get("model") or "").strip()
        if not provider or not model:
            return None
        return {
            "agent_id": agent_ids[0],
            "provider": provider,
            "model": model,
            "preset_id": preset.preset_id,
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[V4] per-agent 模型指定解析失敗（降級為預設）: {e}")
        return None


async def _apply_model_override_credentials(
    current_user: dict,
    credentials: dict,
    model_override: Optional[dict],
) -> tuple[dict, Optional[dict]]:
    """指定別的 provider 時換成該 provider 的 key；換不到就放棄 override。

    回傳 ``(credentials, model_override)``——``model_override`` 被清成 None 表示
    降級回使用者預設模型（不擋聊天、不動 user_api_keys schema）。

    ⚠️ ``resolve_user_llm_credentials(preferred)`` 找不到 preferred 時**不會回
    None**：它會接著掃過其餘所有 provider，回傳第一把有 key 的（見
    api/user_llm.py 的 providers 迴圈）。只判斷 truthy 的話，使用者把 agent 指定
    成 anthropic 卻只有 openai key 時，會把 ``claude-*`` 的模型名配上 openai 的
    key 送出去 → 上游 404，聊天直接壞掉；而那個「無 key 就降級」的分支永遠跑不
    到（完全沒有 key 的人更早就 400 了）。所以必須比對回來的 provider。
    """
    if not model_override or model_override["provider"] == credentials["provider"]:
        return credentials, model_override

    override_credentials = await resolve_user_llm_credentials(
        current_user, model_override["provider"]
    )
    if (
        override_credentials
        and override_credentials.get("provider") == model_override["provider"]
    ):
        return override_credentials, model_override

    logger.warning(
        "[V4] per-agent 模型指定 provider=%s 無 key，降級為使用者預設模型",
        model_override["provider"],
    )
    return credentials, None


async def _router_client_from_selection(
    current_user: dict, selection: dict, default_credentials: dict
):
    """從 {provider, model} 重建 Router 專用 client＋envelope 用完整 selection。

    回傳 ``(client, envelope_selection)``；任一環節失敗 → ``(None, None)``
    （Router 跑使用者預設模型＝現行為，不阻斷聊天）。
    ``envelope_selection`` 含 api_key——**只**放進 transient job envelope
    （Redis stream，與 credentials.api_key 同一信任邊界），不得進 run
    snapshot（會被持久化）或 log。
    """
    try:
        provider = str(selection.get("provider") or "").strip()
        model = str(selection.get("model") or "").strip()
        if not provider or not model:
            return None, None

        router_credentials = default_credentials
        if provider != default_credentials.get("provider"):
            override = await resolve_user_llm_credentials(current_user, provider)
            if not override or override.get("provider") != provider:
                logger.warning(
                    "[V4] router 模型指定 provider=%s 無 key，降級為使用者預設模型",
                    provider,
                )
                return None, None
            router_credentials = override

        client = create_user_llm_client(
            provider=provider,
            api_key=router_credentials["api_key"],
            model=model,
        )
        return client, {
            "provider": provider,
            "model": model,
            "api_key": router_credentials["api_key"],
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[V4] router client 建構失敗（降級為預設）: {e}")
        return None, None


async def _resolve_router_llm_client(
    session: AsyncSession,
    current_user: dict,
    preset_id: Optional[str],
    default_credentials: dict,
):
    """Router 模型可指派（2026-09-09 設計 B2）：特例鍵 "router" 的模型指定。

    回傳 ``(client, envelope_selection)``。graph 啟動前用對應 provider 的
    金鑰建好獨立 client 掛到 manager 供分類呼叫專用——**api_key 不進
    graph state** 的紅線不破（envelope_selection 只走 transient job
    envelope）。任一環節失敗 → ``(None, None)``：Router 跑使用者預設
    模型＝現行為，不阻斷聊天。
    """
    try:
        from core.database.tools import normalize_membership_tier
        from core.feature_flags import agent_presets_enabled
        from core.orm.agent_configs_repo import agent_configs_repo
        from core.orm.agent_presets_repo import agent_presets_repo

        if not agent_presets_enabled():
            return None, None

        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        if tier != "premium":
            return None, None

        user_id = current_user.get("user_id")
        preset = None
        if preset_id:
            preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
        if preset is None:
            preset = await agent_presets_repo.get_default_preset(session, user_id)
        if preset is None:
            return None, None

        config = await agent_configs_repo.get_config(
            session, preset.preset_id, "router"
        )
        selection = config.model_selection if config else None
        if not isinstance(selection, dict):
            return None, None
        return await _router_client_from_selection(
            current_user, selection, default_credentials
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[V4] router 模型指定解析失敗（降級為預設）: {e}")
        return None, None


async def _resolve_agent_preset_config(
    session: AsyncSession,
    current_user: dict,
    preset_id: Optional[str],
    client_enabled_tools: Optional[list],
) -> Optional[dict]:
    """解析 Agent preset → preset_config（design §6.3／impl plan Task B5）。

    - AGENT_PRESETS_ENABLED=off → None（完全走原路徑）。
    - preset_id 指向他人/不存在的 preset → 記 warning、改用 default。
    - 無 default preset → None。
    - 任何例外 → None（降級，不阻斷聊天）。
    - client enabled_tools 在此交集進 tool_names（只能縮小）。
    """
    try:
        from core.feature_flags import agent_presets_enabled

        if not agent_presets_enabled():
            return None

        from core.agents.capability_resolver import resolve_preset_config
        from core.database.tools import normalize_membership_tier
        from core.orm.agent_presets_repo import agent_presets_repo

        user_id = current_user.get("user_id")
        preset = None
        if preset_id:
            preset = await agent_presets_repo.get_preset(session, user_id, preset_id)
            if preset is None:
                logger.warning(
                    "[V4] preset_id=%s not owned by user=%s; falling back to default",
                    preset_id,
                    user_id,
                )
        if preset is None:
            preset = await agent_presets_repo.get_default_preset(session, user_id)
        if preset is None:
            return None

        tier = normalize_membership_tier(current_user.get("membership_tier", "free"))
        # Mixer Step 2：使用者 per-agent 工具勾選（Premium 重驗／失敗降級
        # 皆在 helper 內處理；None = 不套用第 8 層）
        from api.routers.agent_configs import (
            load_agent_prompt_selections,
            load_agent_skill_selections,
            load_agent_tool_selections,
        )

        agent_tool_selections = await load_agent_tool_selections(
            session, current_user, preset.preset_id
        )
        # Mixer Step 4：per-agent skill 白名單（同治理：Premium 重驗、
        # 非空才收、失敗降級 None = 不過濾）
        agent_skill_selections = await load_agent_skill_selections(
            session, current_user, preset.preset_id
        )
        # 2026-09-09 設計：per-agent 自訂 system prompt（同治理對稱）
        agent_prompt_selections = await load_agent_prompt_selections(
            session, current_user, preset.preset_id
        )
        config = resolve_preset_config(
            {
                "preset_id": preset.preset_id,
                "agent_ids": list(preset.agent_ids or []),
                "mode": preset.mode,
                "analysis_mode": preset.analysis_mode,
                "action_policy": preset.action_policy,
                "capability_overrides": dict(preset.capability_overrides or {}),
            },
            user_tier=tier,
            client_enabled_tools=client_enabled_tools,
            agent_tool_selections=agent_tool_selections,
        )
        if config.get("warnings"):
            logger.info(
                "[V4] preset %s warnings: %s", preset.preset_id, config["warnings"]
            )
        config["preset_id"] = preset.preset_id
        # RunMetrics 基準線欄位：claw_loop 記錄 agents= 用
        config["agent_ids"] = list(preset.agent_ids or [])
        # Mixer Step 4：skill 白名單走 preset_config 通道（非敏感中繼資料——
        # api_key 絕不進 graph state，checkpointer 會序列化）
        config["agent_skill_selections"] = agent_skill_selections or {}
        # 2026-09-09 設計：per-agent 自訂 prompt 走同一條通道（純文字、非敏感）
        config["agent_prompt_selections"] = agent_prompt_selections or {}
        return config
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning(f"[V4] preset 解析失敗（降級為無 preset）: {e}")
        return None


@router.post("/api/analyze")
@limiter.limit("10/minute")
async def analyze_crypto(
    request: Request,
    body: QueryRequest,
    current_user: dict = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """
    處理分析請求，以串流 (SSE) 方式回傳結果。

    V4 整合：使用 V4 ManagerAgent 處理所有請求。
    - 一般請求：invoke graph → SSE 串流最終回應
    - HITL 模式：
        第一次觸發 interrupt() → SSE 回傳 {type: "hitl_question"}
        前端帶 resume_answer 重送 → Command(resume=...) 繼續 graph
    若 V4 啟動失敗則回傳 503。
    """
    # session 第一次發訊息才建立，所以「不存在」要放行；存在但屬於別人就當不存在
    # （以前這裡不檢查：拿到別人的 session_id 就能讀他的歷史、往他的對話裡寫）
    owner = await run_sync(get_session_owner, body.session_id)
    if owner is not None and owner != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Session not found")
    resume_context = await _load_resume_model_context(body, current_user)
    resume_selection = resume_context["llm_selection"] if resume_context else None
    credentials = await resolve_user_llm_credentials(
        current_user,
        resume_selection["provider"] if resume_selection else body.user_provider,
    )
    if resume_selection and (
        not credentials or credentials.get("provider") != resume_selection["provider"]
    ):
        raise HTTPException(
            status_code=409, detail="Original model provider key is no longer available"
        )
    if not credentials:
        # 免費配額（2026-09-12）：沒自帶金鑰 → 平台模型（BYOK_FALLBACK_*），每日上限另擋。
        # 2026-09-22 DANNY：所有等級都走本地模型（零成本）；免費會員每日上限看 FREE_DAILY_CHAT_LIMIT（預設 5，可調）、進階不限
        from core.agents.fallback import fallback_credentials
        from core.database.tools import normalize_membership_tier

        credentials = await run_sync(
            lambda: fallback_credentials(
                tier=normalize_membership_tier(
                    current_user.get("membership_tier", "free")
                )
            )
        )
    if not credentials:
        raise HTTPException(
            status_code=400,
            detail="No LLM API key available. Link an AI model in AI Studio → Models.",
        )
    # 平台金鑰日額只套在花錢的雲端 key；本地 local_llama 零成本，免費層由 FREE_DAILY_CHAT_LIMIT 管
    if (
        credentials.get("fallback")
        and not credentials.get("local")
        and not body.resume_answer
    ):
        from core.agents.fallback import fallback_daily_limit

        try:
            used_today = await run_sync(
                lambda: _count_chat_messages_today(current_user["user_id"])
            )
        except Exception:  # noqa: BLE001 — 計數失敗不擋（同免費層的降級）
            used_today = 0
        if used_today >= fallback_daily_limit():
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Free quota reached ({fallback_daily_limit()} messages/day on the platform model). "
                    "Add your own API key in Settings for unlimited use."
                ),
            )

    # ── Model Mixer Step 1：單 agent preset 的 per-agent 模型指定 ──────────
    if resume_context:
        model_override = resume_selection
        user_model_preference = resume_context.get("user_model_preference")
    else:
        model_override = await _resolve_preset_model_override(
            session, current_user, body.preset_id
        )
        credentials, model_override = await _apply_model_override_credentials(
            current_user, credentials, model_override
        )
        user_model_preference = model_override["model"] if model_override else None

    # ── Router 模型可指派（2026-09-09 設計 B2）──────────────────────
    # graph 啟動前建好 Router 專用 client；無設定/失敗 → None（現行為）。
    # resume 輪不重讀 DB（設定可能中途被改）——從 run snapshot 的
    # router_selection 原樣重建，維持該 run 的模型決策一致性
    # （與 user_model_preference 的 resume 語意對稱）。
    router_llm_client, router_selection = None, None
    if not resume_context:
        router_llm_client, router_selection = await _resolve_router_llm_client(
            session, current_user, body.preset_id, credentials
        )
    else:
        snap_router = resume_context.get("router_selection")
        if snap_router:
            router_llm_client, router_selection = await _router_client_from_selection(
                current_user, snap_router, credentials
            )

    # ── 免費每日聊天上限（2026-08 定價 v2，DANNY 核准）──
    # 免費用戶每日最多 FREE_DAILY_CHAT_LIMIT 則 AI 對話；Premium 不限。
    # HITL resume（body.resume_answer）不算新訊息，跳過計數。
    # 用 conversation_history 數「今天 user role 訊息數」（chat 專用，
    # 不與 DM 的 user_message_limits 混用），有 (user_id, timestamp) index。
    if (
        not body.resume_answer
        and current_user.get("membership_tier", "free") != "premium"
    ):
        from core.config import FREE_DAILY_CHAT_LIMIT
        from core.setting_overrides import int_param  # 後台覆寫 > env

        daily_limit = int_param("FREE_DAILY_CHAT_LIMIT", FREE_DAILY_CHAT_LIMIT)
        try:
            user_id = current_user["user_id"]
            sent_today = await run_sync(lambda: _count_chat_messages_today(user_id))
            if sent_today >= daily_limit:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Daily chat limit reached ({daily_limit} messages). "
                        "Upgrade to Premium for unlimited AI analysis."
                    ),
                )
        except HTTPException:
            raise
        except Exception:
            pass  # 計數失敗不擋主流程（降級：允許繼續）

    # 回覆語言照這則訊息的語言（2026-09-28）；看不出來（代號、地址、太短）才用介面語言
    from core.reply_language import resolve_reply_language

    body.language = resolve_reply_language(
        body.message,
        body.language,
        conversation_key=f"web:{current_user.get('user_id')}:{body.session_id}",
    )

    # P0: Chat-specific content filter（寬鬆版，僅擋 spam 與超長訊息）
    filter_result = filter_chat_message(body.message)
    if not filter_result["valid"]:
        logger.warning(
            f"[Security] Chat filter blocked input: {filter_result['warnings']}"
        )
        raise HTTPException(
            status_code=400,
            detail=f"Message could not be processed: {filter_result['warnings'][0]}",
        )

    try:
        # per-agent 指定 > key 上存的 model_selection > request fallback
        # （select_user_llm_model 行為不動，指定存在時跳過它）。
        resolved_model_name: Optional[str] = (
            model_override["model"]
            if model_override
            else select_user_llm_model(
                credentials,
                requested_provider=body.user_provider,
                requested_model=body.user_model,
            )
        )
        # worker 端（scripts/analysis_worker.py）以 credentials["model"] 建
        # client——把最終解析模型寫回，worker 與 in-process 兩條路徑才一致
        # （也讓 worker 開始吃到 request fallback；此前它只看 key 上存的模型）。
        resolved_model_name = resolved_model_name or get_default_model(
            credentials["provider"]
        )
        credentials["model"] = resolved_model_name
        # 回答下方顯示「由哪個模型回答」（平台模型只給品牌名）
        model_label = answer_model_label(credentials["provider"], resolved_model_name)
        user_client = create_user_llm_client(
            provider=credentials["provider"],
            api_key=credentials["api_key"],
            model=resolved_model_name,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"❌ 創建用戶 LLM client 失敗: {e}")
        raise HTTPException(
            status_code=400, detail="Invalid API key, please check your settings"
        )

    logger.info(f"收到分析請求 (Session: {body.session_id}): {body.message[:50]}...")

    try:
        from langgraph.types import Command

        from core.agents.bootstrap import bootstrap
        from core.agents.manager import MANAGER_GRAPH_RECURSION_LIMIT
        from core.database.user import get_user_display_name

        logger.info("✅ ManagerAgent initialized")

        # Principal：讀個人化暱稱（NULL → fallback username）；錢包 = 舊 TON 身份
        # （user_id 即地址）或已驗證的 EVM 地址（evm_ 身份／user_wallets 綁定）
        uid = current_user.get("user_id")
        display_name = (
            await run_sync(get_user_display_name, uid) if uid else None
        ) or current_user.get("username")
        wallet_address, evm_address = await _resolve_principal_wallets(uid)

        # bootstrap() 是同步函式（內部做 PromptRegistry.load、skill 載入、MCP 載入
        # 等 sync I/O），在 async endpoint 內直接呼叫會阻塞 event loop（違反 AGENTS.md
        # 規範），且 MCP loader 內部 asyncio.run() 會因「已在 running loop」而失敗。
        # 走 run_sync bridge 丟到 thread executor（對齊 claw_loop fallback 的 G4 修復）。
        def _do_bootstrap():
            return bootstrap(
                user_client,
                web_mode=True,
                language=body.language,
                user_tier=current_user.get("membership_tier", "free"),
                user_id=uid,
                session_id=body.session_id,
                key_fingerprint=hashlib.sha256(
                    credentials["api_key"].encode()
                ).hexdigest()[:8],
                display_name=display_name,
                wallet_address=wallet_address,
                evm_address=evm_address,
                user_model_preference=user_model_preference,
                router_llm_client=router_llm_client,
            )

        manager = await run_sync(_do_bootstrap)
        config = {
            "configurable": {"thread_id": body.session_id},
            "recursion_limit": MANAGER_GRAPH_RECURSION_LIMIT,
        }

        graph_input: Command
        if body.resume_answer is not None:
            logger.info(f"[V4] HITL resume: session={body.session_id}")
            graph_input = Command(resume=body.resume_answer)
        else:
            # 跨平台共用當前對話：在網頁這個 session 發言 → 標記為 current，
            # 之後切到 Telegram 會接續同一對話（容錯，失敗不影響聊天）。
            await run_sync(
                set_current_session, current_user.get("user_id"), body.session_id
            )

            # 附圖（vision Tier 1，docs/plans/2026-08-27-vision-image-analysis-design.md）：
            # 以使用者 BYOK 模型產生附圖描述 → 併入 query。圖片本身不落庫不進 log；
            # 描述文字隨訊息保存（歷史重播保留圖片語境）。須在 save/history 之前，
            # 讓「排除當前訊息」的比對使用增強後文字。
            user_message_text = body.message
            attachment_id: Optional[int] = None
            if body.image_data_url:
                from api.vision import (
                    VisionError,
                    augment_query_with_image,
                    describe_image,
                    validate_image_data_url,
                )

                try:
                    image_data_url = validate_image_data_url(body.image_data_url)
                    image_description = await describe_image(
                        image_data_url, credentials, body.language
                    )
                except VisionError as exc:
                    # 映射為 VISION_* 錯誤碼（前端 i18n 4 語提示），
                    # 不落入泛用 500——模型不支援/圖片過大需可區別引導
                    raise HTTPException(
                        status_code=exc.status_code, detail=exc.code
                    ) from exc
                user_message_text = augment_query_with_image(
                    body.message, image_description
                )
                # vision Phase 2：壓縮圖存檔（30 天保留、隨對話刪除），
                # attachment_id 藉 message metadata 搭車，歷史重播還原縮圖。
                from api.vision import store_image_attachment

                attachment_id = await store_image_attachment(
                    image_data_url,
                    current_user.get("user_id"),
                    body.session_id,
                )
            _, db_history_raw = await asyncio.gather(
                run_sync(
                    lambda: save_chat_message(
                        "user",
                        # 附圖時為「訊息＋附圖描述」增強版（vision Tier 1）——
                        # 歷史重播保留圖片語境；原圖另存 chat_attachments（Phase 2）
                        user_message_text,
                        session_id=body.session_id,
                        user_id=current_user.get("user_id"),
                        metadata=(
                            {"attachment_id": attachment_id} if attachment_id else None
                        ),
                    )
                ),
                run_sync(
                    lambda: get_chat_history(session_id=body.session_id, limit=20)
                ),
            )

            # Estimate token count (rough: 1 token ≈ 4 chars)
            def estimate_tokens(text: str) -> int:
                return math.ceil(len(text) / 4)

            history_text = ""
            history_load_status = "empty"
            history_truncated = False
            history_token_count = 0
            history_message_count = 0
            try:
                db_history = db_history_raw
                all_lines = []
                for msg in db_history:
                    role = "助手" if msg.get("role") == "assistant" else "用戶"
                    content = (msg.get("content") or "").strip()
                    if content and content != user_message_text:
                        all_lines.append(f"{role}: {content}")

                history_message_count = len(all_lines)

                # Build full history first
                full_history_text = "\n".join(all_lines)

                # Calculate available budget (use context budget from agent config)
                from core.agents.context_budget import CONTEXT_CHAR_BUDGET

                # Leave headroom for the current query (budget // 3), rest for history
                history_budget = CONTEXT_CHAR_BUDGET // 3

                # If history fits, use all. Otherwise smart truncate.
                if estimate_tokens(full_history_text) <= history_budget // 4:
                    history_text = full_history_text
                else:
                    # Smart: keep most recent, plus try to keep at least last 4 exchanges intact
                    # Count from newest backwards until we hit budget
                    lines_rev = list(reversed(all_lines))
                    selected = []
                    accumulated = 0
                    for line in lines_rev:
                        line_tokens = estimate_tokens(line)
                        if (
                            accumulated + line_tokens > history_budget
                            and len(selected) >= 4
                        ):
                            history_truncated = True
                            break
                        selected.append(line)
                        accumulated += line_tokens
                    history_text = "\n".join(reversed(selected))

                history_token_count = estimate_tokens(history_text)

                if not all_lines:
                    history_load_status = "empty"
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                history_load_status = f"error: {e}"
                logger.warning(f"[V4] 載入對話歷史失敗: {e}")

            resolved_system_prompt = body.system_prompt
            resolved_enabled_tools = body.enabled_tools
            if resolved_system_prompt is None or resolved_enabled_tools is None:
                try:
                    user_id = current_user.get("user_id")
                    chat_pref = await analysis_preferences_repo.get_preferences(
                        session, user_id, "chat"
                    )
                    if chat_pref is not None:
                        # system_prompt 不再從偏好解析（2026-09-10 DANNY：全域
                        # prompt 已由 AI Studio per-agent 設定承接，chat 面板
                        # 退役）——這裡只留 enabled_tools 的 legacy fallback。
                        if resolved_enabled_tools is None:
                            resolved_enabled_tools = (
                                list(chat_pref.enabled_tools)
                                if chat_pref.enabled_tools
                                else None
                            )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.warning(f"[V4] 載入使用者偏好失敗: {e}")

            # Agent preset（Phase 2）：server-side 解析工具池（只能縮小）。
            # 任何失敗都降級為「無 preset」（走原路徑），不阻斷聊天。
            preset_config = await _resolve_agent_preset_config(
                session, current_user, body.preset_id, resolved_enabled_tools
            )

            # 過濾 system prompt 的越權/jailbreak 模式(防禦深度)。即使目前
            # state.system_prompt 注入點尚未接線,預先設防避免未來接線時直接放行。
            if resolved_system_prompt:
                from core.agents.prompt_guard import sanitize_system_prompt

                resolved_system_prompt = sanitize_system_prompt(resolved_system_prompt)

            graph_update = {
                "session_id": body.session_id,
                "query": user_message_text,
                "history": history_text,
                "history_load_status": history_load_status,
                "history_truncated": history_truncated,
                "history_token_count": history_token_count,
                "history_message_count": history_message_count,
                "system_prompt": resolved_system_prompt,
                "enabled_tools": resolved_enabled_tools or [],
                "task_results": {},
                "language": body.language,
                "execution_mode": "vending",
            }
            if preset_config is not None:
                if model_override:
                    preset_config["model_override"] = model_override
            # New turns replace optional config, including an explicit clear.
            # Command(resume=...) above deliberately leaves checkpoint config alone.
            graph_update["preset_config"] = preset_config
            graph_update["preset_id"] = (
                preset_config.get("preset_id") if preset_config else None
            )
            # RunMetrics 基準線：本次 run 實際使用的 LLM（未指定模型時與
            # create_user_llm_client 同步用 provider 預設）。
            graph_update["llm_model"] = resolved_model_name or get_default_model(
                credentials["provider"]
            )
            graph_update["llm_provider"] = credentials["provider"]

            graph_input = Command(
                goto="claw_loop",
                update=graph_update,
            )

        async def event_generator_v4():
            # 先綁定：外層 except 會用到，而例外可能發生在建立之前
            run = None
            try:
                progress_queue = asyncio.Queue()
                # 閒置監控要在客戶端走了之後仍然有效，所以進度時間戳與佇列分開：
                # 斷線後停止入列（沒人消費會無限長大），但持續更新時間戳。
                last_activity = {"at": time.monotonic()}
                streaming_to_client = {"value": True}

                def _note_progress(event):
                    last_activity["at"] = time.monotonic()
                    if streaming_to_client["value"]:
                        progress_queue.put_nowait(event)

                def on_progress(event):
                    asyncio.get_running_loop().call_soon_threadsafe(
                        _note_progress, event
                    )

                manager.progress_callback = on_progress

                # 客戶端斷線後想知道「跑到哪了」，需要伺服器端的輸出快照
                run = _create_analysis_run(body.session_id, current_user.get("user_id"))
                run["llm_selection"] = {
                    "provider": credentials["provider"],
                    "model": resolved_model_name,
                }
                run["user_model_preference"] = user_model_preference
                # Router 模型（resume 重建用）：**不含 api_key**——run snapshot
                # 會被持久化；金鑰只在 transient job envelope 裡傳遞。
                run["router_selection"] = (
                    {
                        "provider": router_selection["provider"],
                        "model": router_selection["model"],
                    }
                    if router_selection
                    else None
                )
                _sync_analysis_run(run, force=True)
                yield _emit_run_event(
                    run, {"type": "run_started", "run_id": run["run_id"]}
                )

                # ── Worker dispatch：嘗試把分析交給獨立 worker 進程 ──────────
                # 成功 → 走 Redis pub/sub stream（脫離 gunicorn recycle 威脅）
                # 失敗（Redis 不可用 / worker 不存在）→ 走現有 in-process 路徑
                try:
                    from core.analysis_queue import enqueue_job as _enqueue

                    job = _build_job_envelope(
                        run_id=run["run_id"],
                        session_id=body.session_id,
                        user_id=current_user.get("user_id", ""),
                        credentials=credentials,
                        graph_input=graph_input,
                        config=config,
                        language=getattr(body, "language", None) or "zh-TW",
                        user_tier=current_user.get("membership_tier", "free"),
                        # 跟 in-process 路徑同一組身份（current_user 沒有 wallet_address，
                        # 以前 worker 路徑的 principal 一律沒錢包）
                        display_name=display_name,
                        wallet_address=wallet_address,
                        evm_address=evm_address,
                        web_mode=getattr(body, "web_mode", "web"),
                        key_fingerprint=hashlib.sha256(
                            credentials["api_key"].encode()
                        ).hexdigest()[:8],
                        resume_answer=getattr(body, "resume_answer", None),
                        llm_selection=run["llm_selection"],
                        user_model_preference=user_model_preference,
                        router_selection=router_selection,
                    )
                    if _enqueue(job):
                        logger.info(
                            "[V4] 分析已分派至 worker：run=%s session=%s",
                            run["run_id"],
                            body.session_id,
                        )
                        async for frame in _stream_from_redis(run["run_id"], run):
                            yield frame
                        return
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.info(
                        "[V4] Worker dispatch failed, fallback to in-process: %s", e
                    )

                # ── In-process fallback（現有路徑，不動）─────────────────────────

                # 斷線時 except CancelledError 與 finally 都會呼叫 detach_analysis——
                # 只能交接一次，否則起兩個收尾協程、同一則回覆存兩次
                detached = {"value": False}

                def detach_analysis(reason: str) -> bool:
                    """把仍在跑的分析交給背景收尾，回傳是否真的交接出去。"""
                    if detached["value"]:
                        return True
                    if invoke_task.done():
                        return False
                    detached["value"] = True
                    streaming_to_client["value"] = False
                    run["status"] = "detached"
                    _sync_analysis_run(run, force=True)
                    logger.info(
                        "[V4] %s，分析改在背景繼續：session=%s", reason, body.session_id
                    )
                    _spawn_detached(
                        _finish_analysis_detached(
                            invoke_task,
                            last_activity,
                            run=run,
                            manager=manager,
                            session_id=body.session_id,
                            user_id=current_user.get("user_id"),
                            language=getattr(body, "language", None) or "zh-TW",
                        )
                    )
                    return True

                invoke_task = asyncio.create_task(
                    asyncio.wait_for(
                        manager.graph.ainvoke(graph_input, config),
                        timeout=ANALYSIS_TIMEOUT_SECONDS,
                    )
                )
                # 存進 run dict，讓 POST /api/analyze/{run_id}/revoke 能跨請求 cancel
                run["_invoke_task"] = invoke_task

                # 思考內容也要留下來：串流當下前端有渲染，但重整後歷史是從
                # DB 重建的——不存就必然消失（2026-09-07 DANNY 回報）。
                reasoning_parts: list[str] = []

                try:
                    while not invoke_task.done():
                        queue_task = asyncio.create_task(progress_queue.get())
                        done, pending = await asyncio.wait(
                            [invoke_task, queue_task],
                            timeout=15,
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        if queue_task in done:
                            event = queue_task.result()
                            event_type = event.get("type")
                            if event_type == "token":
                                chunk = event.get("data", {}).get("chunk", "")
                                if chunk:
                                    run["content"] += chunk
                                    _sync_analysis_run(run)
                                    yield _emit_run_event(
                                        run, {"content": chunk, "type": "token"}
                                    )
                            elif event_type == "reasoning":
                                # 思考內容不併進 run["content"]（那是答案本身），
                                # 但要單獨累積，存檔時進 metadata.reasoning
                                chunk = event.get("data", {}).get("chunk", "")
                                if chunk:
                                    reasoning_parts.append(chunk)
                                    yield _emit_run_event(
                                        run, {"content": chunk, "type": "reasoning"}
                                    )
                            else:
                                yield _emit_run_event(
                                    run, {"type": "progress", "data": event}
                                )
                        elif invoke_task in done:
                            queue_task.cancel()
                        else:
                            # 15s 內無事件也無完成 → 發 SSE comment 心跳。
                            # agent 做 tool/LLM 時串流靜默，中間 proxy（Zeabur
                            # ingress / nginx）會砍閒置連線讓使用者以為「斷了」。
                            # SSE comment line（: 開頭）不產生客戶端可見事件，
                            # 但讓 TCP 串流保持活躍。對齊既有重連路徑的 20s 心跳。
                            queue_task.cancel()
                            yield ": keep-alive\n\n"

                    while not progress_queue.empty():
                        event = progress_queue.get_nowait()
                        event_type = event.get("type")
                        if event_type == "token":
                            chunk = event.get("data", {}).get("chunk", "")
                            if chunk:
                                run["content"] += chunk
                                _sync_analysis_run(run)
                                yield _emit_run_event(
                                    run, {"content": chunk, "type": "token"}
                                )
                        elif event_type == "reasoning":
                            # 同上：不進 run["content"]，另外累積存 metadata
                            chunk = event.get("data", {}).get("chunk", "")
                            if chunk:
                                reasoning_parts.append(chunk)
                                yield _emit_run_event(
                                    run, {"content": chunk, "type": "reasoning"}
                                )
                        else:
                            yield _emit_run_event(
                                run, {"type": "progress", "data": event}
                            )

                    result = await invoke_task

                    interrupt_events = result.get("__interrupt__", [])
                    if interrupt_events:
                        iv = interrupt_events[0].value
                        yield _pause_run_for_hitl(run, iv)
                        yield _emit_run_event(run, {"done": True, "waiting": True})
                        return

                    response = result.get("final_response") or empty_response_message(
                        getattr(body, "language", None) or "zh-TW"
                    )
                    response_metadata = build_response_metadata(result)
                    response_metadata["model_label"] = model_label

                    # Save assistant response to DB BEFORE yielding to client.
                    # If server crashes mid-stream, the response is already persisted
                    # and the next request will have full conversation history.
                    # 帶 scam_evidence 進 metadata，供前端歷史還原渲染證據卡片。
                    _scam_ev = response_metadata.get("scam_evidence")
                    _meta = {"model_label": model_label}
                    if _scam_ev:
                        _meta["scam_evidence"] = _scam_ev
                    _reasoning = clip_reasoning("".join(reasoning_parts))
                    if _reasoning:
                        # 歷史還原用：重整後思考區塊才不會整段消失
                        _meta["reasoning"] = _reasoning
                    await run_sync(
                        lambda: save_chat_message(
                            "assistant",
                            response,
                            session_id=body.session_id,
                            user_id=current_user.get("user_id"),
                            metadata=_meta or None,
                        )
                    )

                    # Final type=final event with complete response for client reconstruction
                    yield _emit_run_event(run, {"content": response, "type": "final"})

                    run["content"] = response
                    _finish_analysis_run(run, "completed")

                    yield _emit_run_event(
                        run, {"type": "response_metadata", "data": response_metadata}
                    )
                    yield _emit_run_event(run, {"done": True})

                except asyncio.CancelledError:
                    # 區分兩種 cancel：
                    # (a) 使用者撤銷授權（run["revoked"]==True）→ 真正終止，emit revoked 事件
                    # (b) 客戶端斷線（手機切換 App）→ detach 到背景繼續跑
                    if run.get("revoked"):
                        logger.info(
                            "[V4] agent 因授權撤銷而終止：run=%s", run["run_id"]
                        )
                        _finish_analysis_run(run, "revoked")
                        yield _emit_run_event(
                            run,
                            {
                                "type": "revoked",
                                "message": "Authorization revoked; the agent has been stopped.",
                                "done": True,
                            },
                        )
                        raise
                    # 客戶端斷線：不取消分析（取消等於切出去就白跑）
                    detach_analysis("客戶端斷線")
                    raise
                except asyncio.TimeoutError:
                    logger.error(
                        f"[V4] 分析超時: session={body.session_id}, timeout={ANALYSIS_TIMEOUT_SECONDS}s"
                    )
                    _finish_analysis_run(run, "timeout", error="analysis_timeout")
                    yield _emit_run_event(
                        run,
                        {
                            "error": f"Analysis timed out (over {ANALYSIS_TIMEOUT_SECONDS}s). Please narrow the question and retry.",
                            "done": True,
                        },
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.error(f"[V4] 分析過程發生錯誤: {e}", exc_info=True)
                    safe_msg = safe_analysis_error_message(e)
                    _finish_analysis_run(run, "error", error=safe_msg)
                    yield _emit_run_event(run, {"error": safe_msg, "done": True})
                finally:
                    # 已交接給背景時不要拆掉 progress_callback，
                    # 閒置監控靠它更新時間戳；收尾協程結束時會自行清掉。
                    if detach_analysis("Generator 結束但分析未完成"):
                        pass
                    else:
                        manager.progress_callback = None

            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"[V4] Event generator error: {e}", exc_info=True)
                yield _emit_run_event(
                    run,
                    {
                        "error": "A system error occurred. Please try again later.",
                        "done": True,
                    },
                )

        return StreamingResponse(event_generator_v4(), media_type="text/event-stream")

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except HTTPException:
        # 让语义化状态码（400 VISION_* / 429 上限 / 403 …）原樣送達前端——
        # 不能被下面的 catch-all 吞成 503 泛用訊息（telegram_chat 同款模式）
        raise
    except Exception as bootstrap_err:
        logger.error(f"[V4] Manager 啟動失敗: {bootstrap_err}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Analysis service is temporarily unavailable, please try again later",
        )


@router.get("/api/analyze/status/{run_id}")
async def get_analysis_run_status(
    run_id: str,
    current_user: dict = Depends(get_current_user),
):
    """查詢一次分析的伺服器端狀態與目前累積輸出。

    客戶端斷線（手機切換 App）後用這支取回進度 —— 分析本身不會被取消，
    這裡讓使用者切回來能看到當下的部分輸出，而不是乾等到全部跑完。
    """
    run = _load_analysis_run(run_id)

    # 找不到與不屬於本人一律回 404：不要讓別人試探 run_id 是否存在
    if not run or run.get("user_id") != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Analysis run not found")

    return {
        "success": True,
        "run_id": run["run_id"],
        "session_id": run.get("session_id"),
        "status": run.get("status"),
        "content": run.get("content") or "",
        "error": run.get("error"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
    }


@limiter.limit("10/minute")
@router.post("/api/analyze/{run_id}/revoke")
async def revoke_analysis_run(
    run_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """撤銷執行中 agent 的授權（可信 AI 黑客松第 6 要素：Expiry/Revocation）。

    使用者可隨時撤回授權，agent 立即停止。這是「顯式 revocation」——
    不同於客戶端斷線時的 detach（背景繼續跑），撤銷是真正終止。

    注意：直接讀 _local_analysis_runs 取原 dict（不是 _load_analysis_run 的
    安全副本），因為要操作 _invoke_task 與 revoked flag。
    """
    run = _local_analysis_runs.get(run_id)
    # 找不到與不屬於本人一律回 404（不洩漏 run_id 存在性）。
    # 跨 worker 時本機沒有 → 也可能是別的 worker 在跑，同樣回 404。
    if not run or run.get("user_id") != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Analysis run not found")

    # 已結束的 run 無法撤銷（成功狀態是 completed；waiting 的回答走新 run）
    if (run.get("status") or "") in _SETTLED_RUN_STATUSES:
        raise HTTPException(status_code=409, detail=f"Run already {run.get('status')}")

    # 標記撤銷 + cancel 底層 task
    run["revoked"] = True
    invoke_task = run.get("_invoke_task")
    cancelled = False
    if invoke_task is not None and not invoke_task.done():
        invoke_task.cancel()
        cancelled = True
    elif invoke_task is None:
        # worker 模式：分析跑在獨立進程，本機沒有 task 可 cancel——
        # 走 control channel 通知 worker（scripts/analysis_worker.py 的
        # listen_for_control 收到 revoke 會 cancel 它的 invoke_task）
        from core.analysis_queue import publish_control

        cancelled = await run_sync(publish_control, run_id, "revoke")

    # 寫稽核日誌（agent_revoked 是敏感動作，見 core/audit.py SENSITIVE_ACTIONS）
    from core.audit import AuditLogger

    AuditLogger.log(
        action="agent_revoked",
        user_id=current_user.get("user_id"),
        username=current_user.get("username"),
        resource_type="analysis_run",
        resource_id=run_id,
        endpoint=str(request.url.path),
        method="POST",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={
            "session_id": run.get("session_id"),
            "cancelled_task": cancelled,
            "elapsed_s": round(time.time() - run.get("started_at", time.time()), 1),
        },
    )

    logger.info(
        "[V4] 使用者撤銷 agent 授權：run=%s session=%s cancelled=%s",
        run_id,
        run.get("session_id"),
        cancelled,
    )

    return {"success": True, "run_id": run_id, "revoked": True, "cancelled": cancelled}


@router.get("/api/analyze/stream/{run_id}")
async def resume_analysis_stream(
    run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """斷線後重新接上同一次分析的串流。

    這是 ChatGPT/Claude 的做法：生成不綁在單一連線上，重連時從斷點續傳。
    位置來源有兩個 —— EventSource 自動重連會帶 Last-Event-ID header，
    首次重連則由前端用 ?after= 指定它最後收到的事件 id。

    只有本機（同一個 worker）才有逐筆事件緩衝可重播；WEB_CONCURRENCY > 1 時
    重連可能落到別的 worker，那裡只有共用快取的內容快照，
    此時退化成送出一次 resync（目前完整內容）再等終態。
    """
    header_last_id = request.headers.get("last-event-id")
    after_id = after
    if header_last_id:
        try:
            after_id = max(after_id, int(header_last_id))
        except ValueError:
            pass

    local_run = _local_analysis_runs.get(run_id)
    snapshot = _load_analysis_run(run_id)

    if not snapshot or snapshot.get("user_id") != current_user.get("user_id"):
        raise HTTPException(status_code=404, detail="Analysis run not found")

    async def resume_generator():
        # 這條續傳已送過 hitl_question 就不再補送（前端也會擋，這裡先不重複送）
        hitl_sent = False

        # 1) 補上斷線期間錯過的部分
        if local_run is not None:
            frames, needs_resync = _replay_run_events(local_run, after_id)
            if needs_resync:
                yield f"data: {json.dumps({'type': 'resync', 'content': local_run.get('content') or ''})}\n\n"
            else:
                for frame in frames:
                    hitl_sent = hitl_sent or _is_hitl_question_frame(frame)
                    yield frame
        else:
            # 跨 worker：只有內容快照，一次補齊
            yield f"data: {json.dumps({'type': 'resync', 'content': snapshot.get('content') or ''})}\n\n"

        # waiting 也是終點：暫停等回答時沒有後續事件，不收尾就會永遠掛著
        current = local_run or snapshot
        if (current.get("status") or "") in _SETTLED_RUN_STATUSES:
            for frame in _resume_end_frames(current, hitl_sent=hitl_sent):
                yield frame
            return

        # 2) 還在跑 → 訂閱後續事件
        if local_run is None:
            # 沒有本機事件流可訂閱，改用輪詢共用快取直到終態
            while True:
                await asyncio.sleep(2)
                latest = _load_analysis_run(run_id)
                if not latest:
                    yield f"data: {json.dumps({'done': True, 'status': 'expired'})}\n\n"
                    return
                if (latest.get("status") or "") in _SETTLED_RUN_STATUSES:
                    yield f"data: {json.dumps({'type': 'resync', 'content': latest.get('content') or ''})}\n\n"
                    for frame in _resume_end_frames(latest, hitl_sent=hitl_sent):
                        yield frame
                    return

        queue: asyncio.Queue = asyncio.Queue()
        local_run["_subscribers"].add(queue)
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=_RESUME_HEARTBEAT_SECONDS
                    )
                    hitl_sent = hitl_sent or _is_hitl_question_frame(frame)
                    yield frame
                except asyncio.TimeoutError:
                    # 心跳：讓中間的代理不要因為靜默而關掉連線
                    yield ": keep-alive\n\n"
                    # 靜默可能是 worker 模式的轉發協程已隨原連線結束——
                    # 本機鏡像不會再動，對一下 worker 寫的共用快取
                    _reconcile_stale_local_run(local_run)

                if (
                    local_run.get("status") or ""
                ) in _SETTLED_RUN_STATUSES and queue.empty():
                    for end_frame in _resume_end_frames(local_run, hitl_sent=hitl_sent):
                        yield end_frame
                    return
        finally:
            local_run["_subscribers"].discard(queue)

    return StreamingResponse(resume_generator(), media_type="text/event-stream")


@router.post("/api/chat/clear")
@limiter.limit("5/minute")
async def clear_chat_history_endpoint(
    request: Request,
    session_id: str,
    current_user: dict = Depends(get_current_user),
):
    """清除對話歷史"""
    user_id = current_user.get("user_id")
    owns = await run_sync(check_session_ownership, session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403, detail="You are not authorized to clear this conversation"
        )
    await run_sync(db_clear_history, session_id)

    return {"status": "success", "message": "Chat history cleared"}


# === Codebook Feedback API ===


class FeedbackRequest(BaseModel):
    """分析品質回饋請求"""

    codebook_entry_id: str
    score: int  # 1 = helpful, 0 = not helpful


@router.post("/api/chat/feedback")
@limiter.limit("20/minute")
async def submit_feedback(
    request: Request,
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """儲存分析品質回饋"""
    if body.score not in (0, 1):
        raise HTTPException(status_code=400, detail="Score must be 0 or 1")
    user_id = current_user.get("user_id")
    await run_sync(save_codebook_feedback, body.codebook_entry_id, user_id, body.score)
    return {"success": True}


class ScamConfirmRequest(BaseModel):
    """Trustworthy AI HITL 場景 B：使用者確認已閱讀詐騙判定風險。"""

    session_id: str
    verdict: str  # client 傳值，後端反查 DB 驗證不可被竄改


@router.post("/api/chat/scam-confirm")
@limiter.limit("20/minute")
async def confirm_scam_verdict(
    request: Request,
    body: ScamConfirmRequest,
    current_user: dict = Depends(get_current_user),
):
    """記錄使用者對詐騙判定的風險確認（audit log）。

    安全：不信任 client 傳的 verdict——用 session_id 反查 conversation_history
    最近含 scam_evidence 的 assistant 訊息，比對 verdict 是否相符。
    通過後寫 audit log（action=scam_verdict_confirmed，已列 SENSITIVE_ACTIONS）。
    """
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 確認該 session 屬於當前使用者（與其他 session-scoped endpoint 一致，
    # 防止 IDOR：未授權者無法以別人的 session_id 讀取詐騙判定／汙染稽核軌跡）
    owns = await run_sync(check_session_ownership, body.session_id, user_id)
    if not owns:
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to confirm the scam verdict for this conversation",
        )

    # 反查 DB：該 session 最近幾筆訊息找含 scam_evidence 的
    recent = await run_sync(
        lambda: get_chat_history(session_id=body.session_id, limit=5)
    )
    server_verdict = None
    server_confidence = None
    for msg in recent or []:
        if msg.get("role") != "assistant":
            continue
        meta = msg.get("metadata")
        if isinstance(meta, str):
            try:
                import json

                meta = json.loads(meta)
            except Exception:
                meta = None
        if isinstance(meta, dict) and meta.get("scam_evidence"):
            ev = meta["scam_evidence"]
            server_verdict = ev.get("verdict")
            server_confidence = ev.get("confidence")
            break

    if not server_verdict:
        raise HTTPException(
            status_code=404,
            detail="This conversation has no scam verdict awaiting confirmation",
        )
    if server_verdict != body.verdict:
        # client 傳值與 server 不符（可能是竄改或舊卡片）→ 記錄但用 server 值
        logger.warning(
            "[API] scam-confirm verdict mismatch: client=%s server=%s session=%s",
            body.verdict,
            server_verdict,
            body.session_id,
        )

    from core.audit import AuditLogger

    await run_sync(
        lambda: AuditLogger.log(
            action="scam_verdict_confirmed",
            user_id=user_id,
            resource_type="chat_session",
            resource_id=body.session_id,
            endpoint="/api/chat/scam-confirm",
            method="POST",
            metadata={
                "verdict": server_verdict,
                "confidence": server_confidence,
                "client_verdict": body.verdict,
            },
        )
    )
    return {"success": True}
