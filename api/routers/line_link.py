"""LINE Bot binding & chat endpoints.

design: docs/plans/2026-09-08-line-bot-shared-sessions-design.md

Flow (與 Telegram 同一套模式，見 ``telegram_link.py``):
1. Web user authenticates normally (JWT), calls ``POST /api/line/link-token``
   to get a short-lived HMAC-signed token (5 min TTL). Token 產生／驗證直接
   復用 telegram_link 的實作——那兩個函式只綁 user_id，本來就與平台無關。
2. User sends ``/link <token>`` to the LINE Official Account.
3. LINE Platform POSTs a webhook event to ``/api/line/webhook``. We verify
   the ``X-Line-Signature`` header (HMAC-SHA256 over the raw body with the
   channel secret), then create a ``line_bindings`` row.
4. Subsequent messages run the shared bot chat pipeline (``run_bot_chat``)
   and are answered with a **reply message**.

## 為什麼用 reply 而不是 push

LINE 的 Messaging API pricing 把 push/multicast/broadcast/narrowcast 計入
月額度（免費方案極小），**reply message 不計入**。這個 bot 只回答使用者
主動發來的訊息，全部走 reply ⇒ 零訊息成本。

代價是 reply token 只有 **60 秒**且單次使用。所以：
- webhook handler 立刻回 200（不讓 LINE 判定逾時而重送）
- 真正的工作丟到背景：先送 loading animation（LINE 官方為此情境提供的
  API），再跑 AI，最後用 replyToken 回覆
- 超過 60 秒就回不了了。**不 fallback 到 push**（那要錢）——由呼叫端在
  預算內回一句導向網頁的短訊息。

## 總開關

``LINE_CHANNEL_SECRET`` 未設 ⇒ 所有端點回 404。沒有設定就等於整個功能
不存在，回滾不需要改碼。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.routers.telegram_link import (
    LINK_TOKEN_TTL_SECONDS,
    _extract_token_nonce,
    _is_link_token_consumed,
    _mark_link_token_consumed,
    generate_link_token,
    verify_link_token,
)
from api.utils import logger, run_sync
from core.database import (
    create_line_binding,
    delete_line_binding_by_user_id,
    get_binding_by_line_user_id,
    get_line_active_session,
    get_line_binding_by_user_id,
    get_user_by_id,
    get_user_language,
    update_line_last_used,
)

router = APIRouter(prefix="/api/line", tags=["line"])

LINE_API_BASE = "https://api.line.me/v2/bot"
#: LINE text message 上限 5000 字元；留一點餘裕給截斷提示。
MAX_LINE_RESPONSE_CHARS = 4500
#: reply token 官方保證 60 秒。留 10 秒給網路往返與 LINE 端處理。
REPLY_TOKEN_BUDGET_SECONDS = 50
#: loading animation 的顯示秒數，官方只接受 5 的倍數（5–60）。
LOADING_ANIMATION_SECONDS = 60

_LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
_LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")


def _line_enabled() -> bool:
    """環境變數存在性 = 功能總開關（見模組 docstring）。

    每次呼叫都重讀 env，測試才能在不重載模組的情況下開關它。
    """
    return bool(os.getenv("LINE_CHANNEL_SECRET", _LINE_CHANNEL_SECRET))


def _require_line_enabled() -> None:
    if not _line_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _channel_secret() -> str:
    return os.getenv("LINE_CHANNEL_SECRET", _LINE_CHANNEL_SECRET)


def _channel_access_token() -> str:
    return os.getenv("LINE_CHANNEL_ACCESS_TOKEN", _LINE_CHANNEL_ACCESS_TOKEN)


# ============================================================================
# Webhook signature verification
# ============================================================================


def verify_line_signature(body: bytes, signature: Optional[str]) -> bool:
    """Verify LINE's ``X-Line-Signature`` header.

    LINE 用 channel secret 對**原始 request body** 做 HMAC-SHA256，再 base64
    編碼。必須拿未經 parse 的 bytes——重新序列化過的 JSON 幾乎一定對不上
    （鍵順序、空白、unicode escape 都會變）。

    比對用 ``hmac.compare_digest`` 走定值時間，避免以時間差反推簽章。
    """
    secret = _channel_secret()
    if not secret or not signature:
        return False
    expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("utf-8")
    return hmac.compare_digest(signature, expected)


# ============================================================================
# LINE Messaging API calls (reply = free; we never push)
# ============================================================================


async def _line_post(path: str, payload: dict, *, timeout: float = 10.0) -> bool:
    """呼叫 Messaging API。回傳是否成功（失敗只記錄，不往上拋）。"""
    token = _channel_access_token()
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN unset — skipping %s", path)
        return False
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{LINE_API_BASE}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            logger.warning(
                "LINE %s failed: %s %s", path, resp.status_code, resp.text[:300]
            )
            return False
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 送訊失敗不該炸掉背景工作
        logger.warning("LINE %s error: %s", path, exc)
        return False


async def reply_text(reply_token: str, text: str) -> bool:
    """用 reply token 回覆純文字。**不計入月額度**（見模組 docstring）。"""
    return await _line_post(
        "/message/reply",
        {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


async def show_loading(line_user_id: str) -> bool:
    """顯示「輸入中」動畫，讓使用者知道 AI 還在跑。

    官方 loadingSeconds 只接受 5 的倍數。這支 API 不是送訊息，不計額度。
    """
    return await _line_post(
        "/chat/loading/start",
        {"chatId": line_user_id, "loadingSeconds": LOADING_ANIMATION_SECONDS},
        timeout=5.0,
    )


# ============================================================================
# Pydantic models
# ============================================================================


class LineLinkTokenResponse(BaseModel):
    token: str
    basic_id: str = Field(description="LINE Official Account basic ID (@xxx)")
    expires_in: int


class LineStatusResponse(BaseModel):
    bound: bool
    line_user_id: Optional[str] = None
    display_name: Optional[str] = None
    linked_at: Optional[str] = None


class LineUnlinkResponse(BaseModel):
    success: bool


# ============================================================================
# Web-facing endpoints (JWT)
# ============================================================================


@router.post("/link-token", response_model=LineLinkTokenResponse)
@limiter.limit("10/minute")
async def create_line_link_token(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(_require_line_enabled),
):
    """發一張 5 分鐘的綁定 token，使用者拿去 LINE 對 bot 送 ``/link <token>``。"""
    return LineLinkTokenResponse(
        token=generate_link_token(current_user["user_id"]),
        basic_id=os.getenv("LINE_BASIC_ID", ""),
        expires_in=LINK_TOKEN_TTL_SECONDS,
    )


@router.get("/status", response_model=LineStatusResponse)
async def line_status(
    current_user: dict = Depends(get_current_user),
    _: None = Depends(_require_line_enabled),
):
    binding = await run_sync(get_line_binding_by_user_id, current_user["user_id"])
    if not binding:
        return LineStatusResponse(bound=False)
    return LineStatusResponse(
        bound=True,
        line_user_id=binding["line_user_id"],
        display_name=binding.get("display_name"),
        linked_at=binding.get("linked_at"),
    )


@router.post("/unlink", response_model=LineUnlinkResponse)
@limiter.limit("10/minute")
async def line_unlink(
    request: Request,
    current_user: dict = Depends(get_current_user),
    _: None = Depends(_require_line_enabled),
):
    ok = await run_sync(delete_line_binding_by_user_id, current_user["user_id"])
    return LineUnlinkResponse(success=ok)


# ============================================================================
# Webhook
# ============================================================================


def _line_default_session_id(line_user_id: str) -> str:
    return f"line:{line_user_id}"


async def _handle_link_command(
    line_user_id: str, display_name: Optional[str], token: str, reply_token: str
) -> None:
    """處理 ``/link <token>``——與 Telegram 的 verify-link 同語義。"""
    result = verify_link_token(token)
    if not result:
        await reply_text(reply_token, "連結碼無效或已過期，請回網頁重新產生。")
        return
    matched_user_id, exp_ts = result

    nonce = _extract_token_nonce(token)
    if nonce and await run_sync(_is_link_token_consumed, nonce):
        logger.warning("LINE link rejected: token replay nonce=%s", nonce)
        await reply_text(reply_token, "連結碼無效或已過期，請回網頁重新產生。")
        return

    user = await run_sync(get_user_by_id, matched_user_id)
    if not user or not user.get("is_active"):
        await reply_text(reply_token, "這個帳號目前無法使用，請聯繫客服。")
        return

    ok = await run_sync(
        create_line_binding, line_user_id, matched_user_id, display_name
    )
    if not ok:
        await reply_text(reply_token, "綁定失敗，請稍後再試。")
        return

    if nonce:
        await run_sync(_mark_link_token_consumed, nonce, exp_ts - int(time.time()))
    logger.info(
        "LINE binding created: line_user_id=%s user_id=%s",
        line_user_id,
        matched_user_id,
    )
    await reply_text(reply_token, "綁定成功，現在可以直接在這裡問我問題了。")


async def _handle_text_message(
    line_user_id: str, text: str, reply_token: str, display_name: Optional[str]
) -> None:
    """單一則文字訊息的完整處理（在背景執行，webhook 已先回 200）。"""
    stripped = text.strip()

    if stripped.startswith("/link"):
        parts = stripped.split(maxsplit=1)
        if len(parts) < 2:
            await reply_text(reply_token, "請一起附上連結碼：/link 你的連結碼")
            return
        await _handle_link_command(line_user_id, display_name, parts[1], reply_token)
        return

    binding = await run_sync(get_binding_by_line_user_id, line_user_id)
    if not binding:
        await reply_text(
            reply_token,
            "還沒綁定帳號。請到網頁版的「連結」分頁產生連結碼，"
            "然後在這裡輸入：/link 你的連結碼",
        )
        return

    await show_loading(line_user_id)

    default_session_id = _line_default_session_id(line_user_id)
    session_id = (
        await run_sync(get_line_active_session, line_user_id) or default_session_id
    )

    # 回覆語言：照這則訊息的語言（2026-09-28）；看不出來才用帳號語言（users.language，
    # 網頁切語即寫入），帳號也沒設就繁中。
    from core.reply_language import resolve_reply_language

    language = resolve_reply_language(
        text,
        await run_sync(get_user_language, binding["user_id"]),
        conversation_key=f"line:{line_user_id}:{session_id}",
    )

    from api.routers.telegram_chat import run_bot_chat

    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            run_bot_chat(
                user_id=binding["user_id"],
                session_id=session_id,
                default_session_id=default_session_id,
                default_session_title="LINE Chat",
                message=text,
                language=language,
                max_response_chars=MAX_LINE_RESPONSE_CHARS,
            ),
            timeout=REPLY_TOKEN_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        # reply token 只有 60 秒。**不改用 push**（那要錢）——在預算內回一句
        # 導向網頁的短訊息，長工作留在網頁端繼續。
        logger.info("LINE chat exceeded reply budget for %s", line_user_id)
        await reply_text(
            reply_token,
            "這題需要比較久的分析，已經在跑了。完成後到網頁版就能看到完整結果。",
        )
        return
    except HTTPException as exc:
        if exc.detail == "NO_API_KEY":
            await reply_text(
                reply_token, "還沒設定 AI 模型金鑰，請先到網頁版的 AI Studio 綁定。"
            )
        else:
            await reply_text(reply_token, "處理時發生問題，請稍後再試。")
        return
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 背景工作不該讓例外逃逸
        logger.exception("LINE chat failed: %s", exc)
        await reply_text(reply_token, "處理時發生問題，請稍後再試。")
        return

    logger.info(
        "LINE chat done in %.1fs for %s", time.monotonic() - started, line_user_id
    )
    await reply_text(reply_token, result.response)
    await run_sync(update_line_last_used, line_user_id)


def _text_event_fields(event: dict[str, Any]) -> Optional[tuple[str, str, str]]:
    """1:1 文字訊息 → (line_user_id, text, reply_token)；其他事件回 None。"""
    if event.get("type") != "message":
        return None
    msg = event.get("message") or {}
    if msg.get("type") != "text":
        return None
    source = event.get("source") or {}
    # 1:1 聊天才處理。群組沒有穩定的個人身分可綁，先不支援。
    if source.get("type") != "user":
        return None
    line_user_id = source.get("userId")
    reply_token = event.get("replyToken")
    if not line_user_id or not reply_token:
        return None
    return line_user_id, msg.get("text") or "", reply_token


async def _process_user_events(items: list[tuple[str, str, str]]) -> None:
    # 同一人的訊息共用同一個 session，照順序跑，不讓兩輪同時寫同一串對話
    for line_user_id, text, reply_token in items:
        try:
            await _handle_text_message(line_user_id, text, reply_token, None)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — 一則壞掉不該拖垮整批
            logger.exception("LINE event handling failed: %s", exc)


async def _process_events(events: list[dict[str, Any]]) -> None:
    # 一個 webhook 可能帶多人的訊息。此前整批串行、每則最多跑 ~50 秒，
    # 排在後面的人 reply token（60 秒）早就過期、永遠收不到回覆。
    # 改成依使用者分組、不同人並行。
    by_user: dict[str, list[tuple[str, str, str]]] = {}
    for event in events:
        try:
            fields = _text_event_fields(event)
        except Exception as exc:  # noqa: BLE001 — 格式壞掉的事件跳過
            logger.exception("LINE event parse failed: %s", exc)
            continue
        if fields:
            by_user.setdefault(fields[0], []).append(fields)
    await asyncio.gather(
        *(_process_user_events(items) for items in by_user.values()),
        return_exceptions=True,
    )


@router.post("/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
):
    """LINE Platform 的 webhook 進入點。

    立刻回 200 再背景處理：LINE 對 webhook 有回應時限，慢了會被判定失敗
    並重送，重送的事件會讓同一個問題被回答兩次。
    """
    _require_line_enabled()

    raw_body = await request.body()
    if not verify_line_signature(raw_body, x_line_signature):
        # 401 而不是 403：這是「你沒證明自己是 LINE」而不是「你被禁止」。
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed body") from None

    events = payload.get("events") or []
    if events:
        # fire-and-forget：webhook 這條 HTTP 連線必須馬上結束。
        task = asyncio.create_task(_process_events(events))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"ok": True}


#: 保住背景 task 的強引用——只留區域變數的話會被 GC 中途回收（asyncio 的
#: create_task 只持弱引用），表現成「有時候不回覆」這種難查的間歇性 bug。
_BACKGROUND_TASKS: set[asyncio.Task] = set()
