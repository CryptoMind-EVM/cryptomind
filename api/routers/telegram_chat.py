"""Telegram Bot chat handler — core logic invoked by /api/telegram/chat.

This mirrors ``api/routers/analysis.py`` but without SSE streaming: the
bot receives the final response text and delivers it to the Telegram
user as one (or a few split) messages.

The flow:
1. Resolve ``telegram_id`` -> ``user_id`` via ``telegram_bindings``.
2. Load the user's BYOK LLM credentials (``user_api_keys``).
3. Bootstrap a ManagerAgent (LangGraph) for this user/session.
4. Invoke the graph synchronously and capture the final response.
5. Persist the user message + assistant reply to conversation_history.
6. Return the text to the bot.

Sessions: each Telegram binding gets a single rolling session id of the
form ``tg:{telegram_id}``. This keeps bot chats separate from web chats
while still using the same conversation_history table.

HITL（2026-09-14）：graph 停在 interrupt（記帳同意卡／釐清／loop_fork）時，
不再回「無法產生回覆」——把 payload 經 ``core.bot_hitl.render_hitl`` 轉成
文字＋按鈕放進 ``ChatResponse.hitl``；bot 按鈕打 ``/chat/resume``，或使用者
直接打「好／不要」由 ``run_bot_chat`` 自己認出來走 ``Command(resume=...)``。
checkpointer 是 API 進程內的 MemorySaver（gunicorn workers=1），跨請求有效，
API 重啟後 pending 就沒了（resume 回 409，bot 提示重打）。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

from fastapi import HTTPException

from api.routers.telegram_link import ChatResponse
from api.user_llm import resolve_user_llm_credentials
from api.utils import logger, run_sync
from core.agents.bootstrap import bootstrap
from core.agents.manager import MANAGER_GRAPH_RECURSION_LIMIT
from core.bot_hitl import build_resume_answer, render_hitl, text_decision
from core.database import (
    ensure_session,
    get_binding_by_telegram_id,
    get_chat_history,
    get_telegram_active_session,
    get_user_language,
    save_chat_message,
    set_current_session,
    update_telegram_last_used,
)
from core.i18n import t
from utils.user_client_factory import create_user_llm_client

TG_SESSION_PREFIX = "tg"
MAX_RESPONSE_CHARS = 3500  # leave headroom under Telegram's 4096 limit
HISTORY_LIMIT = 20  # most recent N messages, mirrors web chat
# 回覆被截斷時附在結尾的提示（依使用者語言）
_TRUNCATED_NOTICE = {
    "zh-TW": "…（已截斷，完整內容請至 Web 版查看）",
    "zh-CN": "…（已截断，完整内容请至 Web 版查看）",
    "en": "… (truncated — see the web app for the full reply)",
    "ru": "… (обрезано — полный ответ в веб-версии)",
}


def _tg_session_id(telegram_id: int) -> str:
    return f"{TG_SESSION_PREFIX}:{telegram_id}"


def _build_history_text(session_id: str, current_message: str) -> str:
    """撈回此 session 最近的對話並組成 history 字串，與 Web 端 (analysis.py)
    行為一致：依 token 預算截斷，確保 Telegram 也有上下文記憶。"""
    import math

    from core.agents.context_budget import CONTEXT_CHAR_BUDGET

    def estimate_tokens(text: str) -> int:
        return math.ceil(len(text) / 4)

    try:
        db_history = get_chat_history(session_id=session_id, limit=HISTORY_LIMIT)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Telegram chat: load history failed for session_id=%s: %s",
            session_id,
            exc,
            exc_info=True,
        )
        return ""

    all_lines: list[str] = []
    for msg in db_history:
        role = "助手" if msg.get("role") == "assistant" else "用戶"
        content = (msg.get("content") or "").strip()
        if content and content != current_message:
            all_lines.append(f"{role}: {content}")

    if not all_lines:
        return ""

    full_history_text = "\n".join(all_lines)
    history_budget = CONTEXT_CHAR_BUDGET // 3

    if estimate_tokens(full_history_text) <= history_budget // 4:
        return full_history_text

    # 超出預算：從最新往回保留，至少保留最後 4 行完整交談。
    selected: list[str] = []
    accumulated = 0
    for line in reversed(all_lines):
        line_tokens = estimate_tokens(line)
        if accumulated + line_tokens > history_budget and len(selected) >= 4:
            break
        selected.append(line)
        accumulated += line_tokens
    return "\n".join(reversed(selected))


async def run_telegram_chat(
    telegram_id: int,
    message: str,
    language: str = "zh-TW",
    image_data_url: Optional[str] = None,
) -> ChatResponse:
    binding = await run_sync(get_binding_by_telegram_id, telegram_id)
    if not binding:
        raise HTTPException(
            status_code=403,
            detail="Telegram account is not linked. Please /link first.",
        )

    # Telegram uses its own session namespace; do NOT consult web current_session
    # because that causes cross-platform context bleed (see memory bug 2026-06).
    default_session_id = _tg_session_id(telegram_id)
    session_id = (
        await run_sync(get_telegram_active_session, telegram_id) or default_session_id
    )

    # 回覆語言：照這則訊息的語言（2026-09-28）；看不出來才用帳號語言（users.language，
    # 網頁切語或 /lang 寫入），帳號也沒設就用 bot 從 Telegram 客戶端 language_code 推的預設。
    from core.reply_language import resolve_reply_language

    account_language = await run_sync(get_user_language, binding["user_id"])
    language = resolve_reply_language(
        message,
        account_language,
        default=language,
        conversation_key=f"tg:{telegram_id}:{session_id}",
    )

    response = await run_bot_chat(
        user_id=binding["user_id"],
        session_id=session_id,
        default_session_id=default_session_id,
        default_session_title="Telegram Chat",
        message=message,
        language=language,
        image_data_url=image_data_url,
    )
    await run_sync(update_telegram_last_used, telegram_id)
    return response


async def run_telegram_resume(
    telegram_id: int,
    decision: str,
    option_index: Optional[int] = None,
    text: Optional[str] = None,
) -> ChatResponse:
    """bot 按鈕回答 pending HITL 卡 → 繼續跑 graph（session 解析同 run_telegram_chat）。"""
    binding = await run_sync(get_binding_by_telegram_id, telegram_id)
    if not binding:
        raise HTTPException(
            status_code=403,
            detail="Telegram account is not linked. Please /link first.",
        )
    session_id = await run_sync(
        get_telegram_active_session, telegram_id
    ) or _tg_session_id(telegram_id)
    language = await run_sync(get_user_language, binding["user_id"]) or "zh-TW"
    response = await run_bot_resume(
        user_id=binding["user_id"],
        session_id=session_id,
        language=language,
        decision=decision,
        option_index=option_index,
        text=text,
    )
    await run_sync(update_telegram_last_used, telegram_id)
    return response


async def _bootstrap_manager(user_id: str, session_id: str, language: str):
    """BYOK 憑證 → LLM client → ManagerAgent。chat 與 resume 共用。"""
    fake_user = {"user_id": user_id, "membership_tier": "free", "username": ""}
    credentials = await resolve_user_llm_credentials(fake_user, None)
    if not credentials:
        raise HTTPException(
            status_code=400,
            detail="NO_API_KEY",
        )

    try:
        # 使用用戶在 Web 端儲存的模型（resolve_user_llm_credentials 已回傳）。
        # 若未帶 model，create_user_llm_client 會 fallback 到 provider 預設模型，
        # 而 OpenRouter 預設模型未指定 max_tokens 時會以模型上限(如 65536)預扣額度，
        # 餘額不足即回 402。帶上用戶模型即與 Web 端行為一致。
        user_client = create_user_llm_client(
            provider=credentials["provider"],
            api_key=credentials["api_key"],
            model=credentials.get("model"),
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error("create_user_llm_client failed: %s", exc)
        raise HTTPException(status_code=400, detail="INVALID_API_KEY") from exc

    # bootstrap() 是同步函式，在 async 內直接呼叫會阻塞 loop 且 MCP loader 的
    # asyncio.run() 會失敗。走 run_sync bridge（對齊 analysis.py 的處理）。
    def _do_bootstrap():
        return bootstrap(
            llm_client=user_client,
            web_mode=False,
            language=language,
            user_tier="free",
            user_id=user_id,
            session_id=session_id,
            key_fingerprint=hashlib.sha256(credentials["api_key"].encode()).hexdigest()[
                :8
            ],
        )

    manager = await run_sync(_do_bootstrap)
    return manager, credentials


def _graph_config(session_id: str) -> dict:
    return {
        "configurable": {"thread_id": session_id},
        "recursion_limit": MANAGER_GRAPH_RECURSION_LIMIT,
    }


def _pending_interrupt(manager, session_id: str) -> Optional[dict]:
    """這個 session 的 graph 是否停在 interrupt；是就回 payload。"""
    try:
        state = manager.graph.get_state(_graph_config(session_id))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 查不到就當沒有 pending
        logger.debug("get_state failed for %s: %s", session_id, exc)
        return None
    interrupts = getattr(state, "interrupts", None) or ()
    for iv in interrupts:
        value = getattr(iv, "value", None)
        if isinstance(value, dict):
            return value
    return None


async def _run_graph_and_deliver(
    manager,
    graph_input,
    *,
    user_id: str,
    session_id: str,
    language: str,
    max_response_chars: int,
) -> ChatResponse:
    """跑 graph → interrupt 就回 hitl，否則存 assistant 回覆並截斷。chat／resume 共用。"""
    try:
        result = await manager.graph.ainvoke(
            graph_input, config=_graph_config(session_id)
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.exception("graph.ainvoke failed: %s", exc)
        raise HTTPException(status_code=500, detail="ANALYSIS_FAILED") from exc

    interrupt_events = result.get("__interrupt__") or []
    if interrupt_events:
        payload = getattr(interrupt_events[0], "value", None)
        hitl = render_hitl(payload, language)
        # 卡片不存歷史（網頁版同樣只 emit 事件不落庫）；current session 仍要標，
        # 之後到網頁打開能看到同一段對話。
        await run_sync(set_current_session, user_id, session_id)
        return ChatResponse(
            success=True, response=hitl["text"], session_id=session_id, hitl=hitl
        )

    final_response = result.get("final_response") or ""
    if not final_response:
        logger.warning(
            "Telegram chat: empty final_response, result keys=%s", list(result.keys())
        )
        final_response = t("errors.analysis.no_response", language)

    await run_sync(save_chat_message, "assistant", final_response, session_id, user_id)
    # 更新跨平台共用的當前對話 → 之後在網頁打開會接續這個 bot 對話。
    await run_sync(set_current_session, user_id, session_id)

    # 上限依平台而異（Telegram 4096、LINE 5000），由呼叫端指定。
    if len(final_response) > max_response_chars:
        final_response = (
            final_response[:max_response_chars]
            + "\n\n"
            + _TRUNCATED_NOTICE.get(language, _TRUNCATED_NOTICE["zh-TW"])
        )

    return ChatResponse(
        success=True,
        response=final_response,
        session_id=session_id,
    )


async def run_bot_resume(
    *,
    user_id: str,
    session_id: str,
    language: str,
    decision: str,
    option_index: Optional[int] = None,
    text: Optional[str] = None,
    max_response_chars: int = MAX_RESPONSE_CHARS,
) -> ChatResponse:
    """對 pending interrupt 送答案並繼續跑。沒有 pending → 409。"""
    manager, _ = await _bootstrap_manager(user_id, session_id, language)
    payload = await run_sync(_pending_interrupt, manager, session_id)
    if payload is None:
        raise HTTPException(status_code=409, detail="NO_PENDING_HITL")

    from langgraph.types import Command  # local import, heavy

    answer = build_resume_answer(
        payload, decision, option_index=option_index, text=text
    )
    graph_input = Command(resume=answer)
    return await _run_graph_and_deliver(
        manager,
        graph_input,
        user_id=user_id,
        session_id=session_id,
        language=language,
        max_response_chars=max_response_chars,
    )


async def run_bot_chat(
    *,
    user_id: str,
    session_id: str,
    default_session_id: str,
    default_session_title: str,
    message: str,
    language: str = "zh-TW",
    image_data_url: Optional[str] = None,
    max_response_chars: int = MAX_RESPONSE_CHARS,
) -> ChatResponse:
    """平台無關的 bot 對話管線（Telegram 與 LINE 共用）。

    呼叫端負責「平台身分 → user_id」與 session 命名空間；這裡只管
    BYOK 金鑰、附圖、歷史、跑 graph、存訊息、截斷。抽出來是為了 LINE
    不必再複製一份兩百多行的相同流程（2026-09-08）。

    ``default_session_id`` 用來判斷「這是不是該平台的預設滾動 session」
    ——只有它需要由 bot 端建立；使用者切過去的 Web session 已經存在，
    再 create 一次會覆蓋掉它的標題。
    """
    manager, credentials = await _bootstrap_manager(user_id, session_id, language)

    # 停在同意卡時使用者直接打「好／不要」（LINE 沒按鈕；Telegram 也可能打字）
    # → 當成回答卡，不開新問題。其他文字照舊當新問題（graph 會從頭跑）。
    if not image_data_url:
        pending = await run_sync(_pending_interrupt, manager, session_id)
        if pending is not None:
            picked = text_decision(pending, message)
            if picked is not None:
                await run_sync(
                    save_chat_message, "user", message, session_id, user_id, None
                )
                return await run_bot_resume(
                    user_id=user_id,
                    session_id=session_id,
                    language=language,
                    decision=picked["decision"],
                    option_index=picked.get("option_index"),
                    text=picked.get("text"),
                    max_response_chars=max_response_chars,
                )

    # 附圖（vision Tier 1）：驗證 + 以使用者 BYOK 模型描述 → 併入訊息。
    # 原圖另存 chat_attachments（Phase 2，30 天保留、隨對話刪除）；
    # 描述文字隨訊息保存（保留圖片語境）。
    attachment_id = None
    if image_data_url:
        from api.vision import (
            VisionError,
            augment_query_with_image,
            describe_image,
            validate_image_data_url,
        )

        try:
            image_data_url = validate_image_data_url(image_data_url)
            image_description = await describe_image(
                image_data_url, credentials, language
            )
        except VisionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
        message = augment_query_with_image(message, image_description)

    # 只有預設 session 需要由 bot 端建立；切換到的 Web session 已存在，
    # 不要覆蓋它的標題。這條路徑每則訊息都會走——必須冪等：裸 INSERT 的
    # create_session 在第二則訊息就撞 sessions_pkey（2026-09-09 LINE 首位
    # 真人使用者踩爆；Telegram 路徑此前無人生產使用）。
    if session_id == default_session_id:
        await run_sync(ensure_session, session_id, default_session_title, user_id)
    # 先撈歷史（此時當前訊息尚未寫入，不會被自己污染），再存當前訊息。
    history_text = await run_sync(_build_history_text, session_id, message)
    if image_data_url:
        # vision Phase 2：壓縮圖存檔（best-effort，失敗不擋回覆），
        # attachment_id 藉 message metadata 搭車，歷史重播還原縮圖。
        from api.vision import store_image_attachment

        attachment_id = await store_image_attachment(
            image_data_url, user_id, session_id
        )
    await run_sync(
        save_chat_message,
        "user",
        message,
        session_id,
        user_id,
        {"attachment_id": attachment_id} if attachment_id else None,
    )

    from langgraph.types import Command  # local import, heavy

    graph_input = Command(
        goto="claw_loop",
        update={
            "session_id": session_id,
            "query": message,
            "language": language,
            "history": history_text,
        },
    )
    return await _run_graph_and_deliver(
        manager,
        graph_input,
        user_id=user_id,
        session_id=session_id,
        language=language,
        max_response_chars=max_response_chars,
    )
