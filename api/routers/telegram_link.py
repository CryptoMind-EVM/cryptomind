"""Telegram Bot binding & chat endpoints.

Flow:
1. Web user authenticates normally (JWT), calls ``POST /api/telegram/link-token``
   to get a short-lived HMAC-signed token (5 min TTL) plus a one-tap deep link
   ``https://t.me/<bot>?start=link_<code>`` (PR-7, 2026-09-27).
2. User taps the deep link (Telegram sends ``/start link_<code>``) or sends
   ``/link <token>`` to the bot by hand.
3. Bot calls ``POST /api/telegram/link-preview`` (no binding yet) and shows
   who the Telegram will be bound to, with [Confirm] [Cancel] buttons.
4. Confirm → ``POST /api/telegram/verify-link`` with the pending id from the
   same Telegram user → server creates the ``telegram_bindings`` row.
   Cancel → ``POST /api/telegram/link-cancel`` invalidates the link.
5. Subsequent bot messages hit ``POST /api/telegram/chat`` which resolves
   the binding, loads the user's BYOK api key, and runs the ManagerAgent
   graph directly (no SSE — returns the final text).

Auth model:
- ``link-token`` and ``status`` and ``unlink`` require a valid platform
  JWT (web user).
- ``link-preview``／``verify-link``／``link-cancel`` and ``chat`` are internal
  bot endpoints authenticated
  with ``X-Bot-Secret`` (a shared secret from env ``BOT_INTERNAL_SECRET``).
  They never accept a user JWT — the bot proves who the user is by
  presenting a valid link token / binding row.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import SECRET_KEY, get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger, run_sync
from core.database import (
    check_session_ownership,
    create_telegram_binding,
    delete_binding_by_user_id,
    get_binding_by_telegram_id,
    get_binding_by_user_id,
    get_cache,
    get_current_session,
    get_sessions,
    get_telegram_active_session,
    get_user_by_id,
    set_cache,
    set_current_session,
    set_telegram_active_session,
)
from core.database.cache import consume_cache, purge_cache_prefix
from core.database.user import get_user_display_name

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

LINK_TOKEN_TTL_SECONDS = 5 * 60  # 5 minutes
_BOT_INTERNAL_SECRET = os.getenv("BOT_INTERNAL_SECRET", "")

if not _BOT_INTERNAL_SECRET and os.getenv("ENVIRONMENT", "").lower() in (
    "production",
    "prod",
):
    logger.warning(
        "BOT_INTERNAL_SECRET is empty in production — Telegram bot endpoints "
        "will reject all requests. Set BOT_INTERNAL_SECRET in the environment."
    )


# ============================================================================
# Internal auth dependency
# ============================================================================


def _require_bot_secret(x_bot_secret: Optional[str] = Header(default=None)) -> None:
    """Authenticate internal bot-to-API calls.

    The bot service shares a secret with the API via the
    ``BOT_INTERNAL_SECRET`` env var. In production this MUST be set; in
    development an empty secret is tolerated so local testing is easy.
    """
    expected = _BOT_INTERNAL_SECRET
    if not expected:
        if os.getenv("ENVIRONMENT", "").lower() in ("production", "prod"):
            raise HTTPException(status_code=503, detail="Bot service not configured")
        return  # dev mode: allow empty secret
    if not x_bot_secret or not hmac.compare_digest(x_bot_secret, expected):
        raise HTTPException(status_code=401, detail="Invalid bot secret")


# ============================================================================
# Link token generation / verification (HMAC-signed, stateless)
# ============================================================================


def _get_jwt_secret() -> str:
    """Get JWT secret from env, allowing test-time override via JWT_SECRET_KEY."""
    return os.environ.get("JWT_SECRET_KEY", SECRET_KEY)


def generate_link_token(user_id: str) -> str:
    """Issue a stateless, HMAC-signed link token.

    Format: ``{b64uid}.{exp_ts}.{nonce}.{hmac_hex}``
    ``b64uid`` is a base64url encoding of the user_id so the verifying
    side can recover the target user without a brute-force scan. The
    HMAC covers ``b64uid.exp_ts.nonce`` so the token cannot be
    re-targeted, extended, or replayed after expiry.
    """
    import base64

    b64uid = base64.urlsafe_b64encode(user_id.encode()).decode().rstrip("=")
    exp_ts = int(time.time()) + LINK_TOKEN_TTL_SECONDS
    nonce = secrets.token_hex(8)
    payload = f"{b64uid}.{exp_ts}.{nonce}"
    sig = hmac.new(
        _get_jwt_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{sig}"


def verify_link_token(token: str) -> Optional[tuple[str, int]]:
    """Verify a link token and recover the (user_id, exp_ts) it carries.

    Returns ``(user_id, exp_ts)`` if the token is valid and not expired,
    otherwise ``None``.
    """
    import base64

    try:
        b64uid, exp_ts_str, nonce, sig = token.split(".", 3)
    except ValueError:
        return None

    try:
        exp_ts = int(exp_ts_str)
    except ValueError:
        return None
    if exp_ts < int(time.time()):
        return None

    payload = f"{b64uid}.{exp_ts_str}.{nonce}"
    expected_sig = hmac.new(
        _get_jwt_secret().encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Decode user_id (add back padding stripped during encoding).
    padding = "=" * (-len(b64uid) % 4)
    try:
        user_id = base64.urlsafe_b64decode(b64uid + padding).decode()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None
    return user_id, exp_ts


# ============================================================================
# One-time link token enforcement
# ============================================================================
#
# The link token is a stateless HMAC bearer credential with a 5-min TTL. To
# stop a leaked token being replayed within that window (an attacker binding
# their own Telegram to the victim's account), we mark each token's nonce as
# consumed after a successful bind and reject any re-use. Backed by the shared
# cache (Redis → DB fallback). Fail-open on cache errors: a cache glitch must
# never block a legitimate first-time bind.

_LINK_TOKEN_USED_PREFIX = "tglink_used:"


def _extract_token_nonce(token: str) -> Optional[str]:
    """Pull the nonce out of a ``{b64uid}.{exp}.{nonce}.{sig}`` token."""
    parts = token.split(".", 3)
    return parts[2] if len(parts) == 4 else None


def _is_link_token_consumed(nonce: str) -> bool:
    try:
        return get_cache(f"{_LINK_TOKEN_USED_PREFIX}{nonce}") is not None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — fail-open, never block a real bind
        logger.warning("link-token consumed check failed (allowing): %s", exc)
        return False


def _mark_link_token_consumed(nonce: str, ttl_seconds: int) -> None:
    try:
        set_cache(f"{_LINK_TOKEN_USED_PREFIX}{nonce}", "1", ttl=max(ttl_seconds, 60))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("link-token mark-consumed failed: %s", exc)


# ============================================================================
# 一鍵綁定短碼（deep link ``t.me/<bot>?start=link_<code>``，PR-7 2026-09-27）
# ============================================================================
#
# Telegram 的 start 參數只收 [A-Za-z0-9_-]、最多 64 字；完整 token 含 "." 又超過 64 字，
# 放不進去。所以另發一個隨機短碼，存共用快取「短碼 → 完整 token」。綁定對象仍由
# token 內簽過的 user_id 決定。
#
# 確認步驟（安全需求，lead 2026-09-27）：攻擊者可以把「自己帳號」的連結丟給受害者，
# 一按就綁 → 受害者的 Telegram 接到攻擊者帳號 → web↔Telegram 共用 current_session_id，
# 攻擊者在網頁讀得到受害者的 bot 對話。所以 /start link_<code> 與 /link <token> 都只
# 「預覽」：link-preview 驗 token、建一筆待確認（key＝token 的 nonce，綁定這個 telegram_id），
# bot 顯示要綁給誰＋警告＋[確認][取消]；verify-link 收到同一個 Telegram 使用者的確認才綁。
#
# - 待確認以 nonce 當 key：同一張 token 只會有一筆（再預覽會蓋掉），callback_data 放得下
#   （"tgl:ok:" + 16 hex = 23 bytes），也不會把完整 token 放進按鈕。
# - 一次性：確認時用 consume_cache 搶這筆待確認（DB 刪除筆數為準，併發只有一個成功），
#   同時消耗短碼、標記 nonce 已用；取消也一樣作廢。
# - DB 那層快取不吃 TTL，過期由完整 token 的 exp 擋（預覽、確認各驗一次）。
# - 快取失敗＝不給 deep link／不能確認（fail-closed）。

START_PAYLOAD_PREFIX = "link_"
_START_CODE_PREFIX = "tglink_start:"
_PENDING_PREFIX = "tglink_pending:"
_START_PAYLOAD_MAX = 64
# token_urlsafe(24) 產 32 字；驗證時放寬到 16～59（加前綴仍 ≤ 64），擋掉亂打的
_START_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{16,59}$")
_PENDING_ID_PATTERN = r"^[0-9a-f]{16}$"  # = generate_link_token 的 nonce


def build_deep_link(bot_username: str, code: str) -> Optional[str]:
    """``https://t.me/<bot>?start=link_<code>``；參數不合 Telegram 規格就回 None。"""
    bot = (bot_username or "").strip().lstrip("@")
    payload = f"{START_PAYLOAD_PREFIX}{code}"
    if not bot or len(payload) > _START_PAYLOAD_MAX:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]+", payload):
        return None
    return f"https://t.me/{bot}?start={payload}"


def _prune_stale_link_rows() -> None:
    """DB 快取不吃 TTL：發新碼時順手清掉 10 分鐘前的短碼／待確認／nonce 列。

    token 5 分鐘就過期（HMAC 內的 exp），超過 10 分鐘的列已經不可能用到；
    清不掉只是多幾列，不擋這次發碼。
    """
    for prefix in (_START_CODE_PREFIX, _PENDING_PREFIX, _LINK_TOKEN_USED_PREFIX):
        try:
            purge_cache_prefix(prefix, max(600, LINK_TOKEN_TTL_SECONDS * 2))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — 清理失敗不影響綁定
            logger.info("link cache prune skipped: %s", type(exc).__name__)


def _issue_start_code(full_token: str) -> Optional[str]:
    _prune_stale_link_rows()
    code = secrets.token_urlsafe(24)
    try:
        set_cache(
            f"{_START_CODE_PREFIX}{code}", {"t": full_token}, ttl=LINK_TOKEN_TTL_SECONDS
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 沒有 deep link 就退回手動指令
        logger.warning("link start-code store failed: %s", type(exc).__name__)
        return None
    return code


def _resolve_link_token(token: str) -> tuple[Optional[str], Optional[str]]:
    """``(完整 token, 短碼)``。有 "." ＝手動貼的完整 token；否則當短碼查快取（只讀不消耗）。"""
    token = (token or "").strip()
    if "." in token:
        return token, None
    if not _START_CODE_RE.fullmatch(token):
        return None, None
    try:
        value = get_cache(f"{_START_CODE_PREFIX}{token}")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed：手動 /link 仍可用
        logger.warning("link start-code lookup failed: %s", type(exc).__name__)
        return None, None
    if isinstance(value, dict) and isinstance(value.get("t"), str):
        return value["t"], token
    return None, None


def _mask_account(user_id: str) -> str:
    """帳號遮罩：``evm_0x12ab…`` → ``0x12…ab``；其他 id 留頭 4 尾 2。"""
    uid = user_id or ""
    if uid.startswith("evm_0x") and len(uid) > 10:
        addr = uid[len("evm_") :]
        return f"{addr[:4]}…{addr[-2:]}"
    if len(uid) > 8:
        return f"{uid[:4]}…{uid[-2:]}"
    return f"{uid[:2]}…"


def _account_label(user_id: str) -> str:
    """確認訊息裡的「哪個帳號」：顯示名稱（或 username）＋遮罩過的帳號 id。"""
    name = None
    try:
        name = get_user_display_name(user_id)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:  # noqa: BLE001 — 沒有顯示名稱就用 username
        name = None
    if not name:
        name = (get_user_by_id(user_id) or {}).get("username")
    name = (name or "").strip()[:40]
    masked = _mask_account(user_id)
    return f"{name} ({masked})" if name else masked


def _store_pending(pending_id: str, value: dict, ttl: int) -> bool:
    try:
        set_cache(f"{_PENDING_PREFIX}{pending_id}", value, ttl=ttl)
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 存不進去就不能確認（fail-closed）
        logger.warning("link pending store failed: %s", type(exc).__name__)
        return False


def _load_pending(pending_id: str) -> Optional[dict]:
    try:
        value = get_cache(f"{_PENDING_PREFIX}{pending_id}")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("link pending lookup failed: %s", type(exc).__name__)
        return None
    if (
        isinstance(value, dict)
        and isinstance(value.get("t"), str)
        and isinstance(value.get("tg"), int)
    ):
        return value
    return None


def _claim_pending(pending_id: str, pending: dict) -> bool:
    """搶這筆待確認並作廢它的短碼：True＝這次請求拿到（DB 刪除筆數為準，只有一個會成功）。"""
    try:
        if not consume_cache(f"{_PENDING_PREFIX}{pending_id}"):
            return False
        if pending.get("code"):
            consume_cache(f"{_START_CODE_PREFIX}{pending['code']}")
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — fail-closed
        logger.warning("link pending claim failed: %s", type(exc).__name__)
        return False


def _brief_after_bind(user_id: str) -> tuple[bool, Optional[int]]:
    """綁定後早報會不會送到 Telegram、幾點。

    不寫任何東西：沒偏好列的人「綁 TG 就開」是送出當下由 LEFT JOIN 推的
    （core/daily_brief/schedule.effective_prefs），晚綁的人一樣適用；有明確偏好的照偏好。
    """
    from core.daily_brief import store as brief_store
    from core.daily_brief.schedule import effective_prefs

    try:
        row = brief_store.get_prefs_row(user_id)
        if not row:
            return False, None
        prefs = effective_prefs(row)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 查不到早報狀態不影響綁定
        logger.warning("brief status after bind failed: %s", type(exc).__name__)
        return False, None
    on = (
        prefs.enabled and "telegram" in prefs.channels and prefs.telegram_id is not None
    )
    return on, (prefs.send_hour if on else None)


# ============================================================================
# Pydantic models
# ============================================================================


class LinkTokenResponse(BaseModel):
    token: str
    bot_username: str = Field(description="Bot @username to send /link to")
    expires_in: int = Field(description="Token TTL in seconds")
    deep_link: Optional[str] = Field(
        default=None, description="One-tap t.me link; null when unavailable"
    )


class LinkPreviewRequest(BaseModel):
    # 完整 token（/link 手動貼）或 deep link 的短碼（/start link_<code>）
    token: str = Field(min_length=1, max_length=512)
    telegram_id: int


class LinkPreviewResponse(BaseModel):
    pending_id: str
    # 「要綁給誰」：顯示名稱＋遮罩帳號 id（例：Danny (0x12…ab)）
    target_label: str
    # 這支 Telegram 目前綁在「別的」帳號時才有值 → 確認文字要講「從 A 移到 B」
    current_label: Optional[str] = None
    expires_in: int


class VerifyLinkRequest(BaseModel):
    # 確認鈕：link-preview 回的 pending_id；不再收 token（舊 bot 直接送 token → 422，不會綁）
    pending_id: str = Field(pattern=_PENDING_ID_PATTERN)
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None


class LinkCancelRequest(BaseModel):
    pending_id: str = Field(pattern=_PENDING_ID_PATTERN)
    telegram_id: int


class VerifyLinkResponse(BaseModel):
    success: bool
    user_id: str
    username: str
    # 綁定後早報是否會送到這個 Telegram、本地幾點（bot 成功訊息用）
    brief_enabled: bool = False
    brief_hour: Optional[int] = None


class TelegramStatusResponse(BaseModel):
    bound: bool
    telegram_id: Optional[int] = None
    telegram_username: Optional[str] = None
    linked_at: Optional[str] = None


class ChatRequest(BaseModel):
    telegram_id: int
    message: str
    language: str = "zh-TW"
    # 附圖（vision Tier 1，docs/plans/2026-08-27-vision-image-analysis-design.md）：
    # data URL；綁定用戶限定，伺服器驗證後轉文字描述，圖片本身不落庫
    image_data_url: Optional[str] = Field(None, max_length=5_600_000)


class ChatResponse(BaseModel):
    success: bool
    response: str
    session_id: str
    # graph 停在 interrupt（同意卡／釐清／loop_fork）時帶回：
    # {type, text, buttons:[{label, decision, index?}]}，見 core/bot_hitl.py。
    # response 同時放卡片文字，讓沒有按鈕的平台（LINE）也看得到問題。
    hitl: Optional[dict] = None


class ResumeRequest(BaseModel):
    """bot 端對 pending interrupt 的回答（按鈕或打字）。"""

    telegram_id: int
    decision: str  # approve / deny / option / text / wrap
    option_index: Optional[int] = None
    text: Optional[str] = None


class SessionsRequest(BaseModel):
    telegram_id: int


class BriefToggleRequest(BaseModel):
    telegram_id: int
    enabled: bool


class LanguageSetRequest(BaseModel):
    telegram_id: int
    language: str = Field(..., max_length=8)


class SessionItem(BaseModel):
    id: str
    title: str
    updated_at: Optional[str] = None
    is_active: bool = False


class SessionsResponse(BaseModel):
    success: bool
    sessions: list[SessionItem]
    active_session_id: Optional[str] = None


class UseSessionRequest(BaseModel):
    telegram_id: int
    # None / "" 表示切回預設的 tg:{telegram_id} 滾動 session。
    session_id: Optional[str] = None


class UseSessionResponse(BaseModel):
    success: bool
    active_session_id: Optional[str] = None


# ============================================================================
# Web-facing endpoints (require platform JWT)
# ============================================================================


@router.get("/status", response_model=TelegramStatusResponse)
async def get_telegram_status(current_user: dict = Depends(get_current_user)):
    """Check whether the current web user has a Telegram account bound."""
    binding = await run_sync(get_binding_by_user_id, current_user["user_id"])
    if not binding:
        return TelegramStatusResponse(bound=False)
    return TelegramStatusResponse(
        bound=True,
        telegram_id=binding["telegram_id"],
        telegram_username=binding.get("username"),
        linked_at=binding.get("linked_at"),
    )


@router.post("/link-token", response_model=LinkTokenResponse)
@limiter.limit("5/minute")
async def create_link_token(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Issue a short-lived link token for the current user.

    The user taps ``deep_link`` (one tap) or sends ``/link <token>`` to the bot.
    """
    token = generate_link_token(current_user["user_id"])
    bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "").lstrip("@")
    deep_link = None
    if bot_username:
        code = await run_sync(_issue_start_code, token)
        deep_link = build_deep_link(bot_username, code) if code else None
    return LinkTokenResponse(
        token=token,
        bot_username=bot_username,
        expires_in=LINK_TOKEN_TTL_SECONDS,
        deep_link=deep_link,
    )


@router.post("/unlink", response_model=TelegramStatusResponse)
@limiter.limit("5/minute")
async def unlink_telegram(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Remove the Telegram binding for the current user."""
    deleted = await run_sync(delete_binding_by_user_id, current_user["user_id"])
    logger.info(
        "Telegram unlink: user=%s deleted=%s",
        current_user["user_id"],
        deleted,
    )
    return TelegramStatusResponse(bound=False)


# ============================================================================
# Bot-facing endpoints (require X-Bot-Secret header)
# ============================================================================


_INVALID_LINK = "Invalid or expired link token."
# bot 端靠這個 detail 分辨「不是你的確認」與「帳號停用」（兩個都是 403）
LINK_NOT_YOURS = "LINK_NOT_YOURS"


async def _check_link_token(token: str) -> tuple[str, int, str]:
    """HMAC、過期、nonce 未用過 → ``(user_id, exp_ts, nonce)``；不合就 400。"""
    result = verify_link_token(token)
    nonce = _extract_token_nonce(token)
    if not result or not nonce:
        raise HTTPException(status_code=400, detail=_INVALID_LINK)
    # 一次性：拒絕已消費過的 token（防 5 分鐘內重放 → 帳號被綁走）。不記 nonce（token 的一部分）
    if await run_sync(_is_link_token_consumed, nonce):
        logger.warning("Telegram link rejected: token already used")
        raise HTTPException(status_code=400, detail=_INVALID_LINK)
    return result[0], result[1], nonce


async def _load_owned_pending(pending_id: str, telegram_id: int) -> dict:
    """待確認要存在、而且是同一個 Telegram 使用者開的（別人按確認／取消一律 403）。"""
    pending = await run_sync(_load_pending, pending_id)
    if not pending:
        raise HTTPException(status_code=400, detail=_INVALID_LINK)
    if pending["tg"] != telegram_id:
        logger.warning("Telegram link confirm by a different telegram user rejected")
        raise HTTPException(status_code=403, detail=LINK_NOT_YOURS)
    return pending


@router.post("/link-preview", response_model=LinkPreviewResponse)
@limiter.limit("10/minute")
async def link_preview(
    request: Request,
    body: LinkPreviewRequest,
    _: None = Depends(_require_bot_secret),
):
    """``/start link_<code>`` 或 ``/link <token>``：驗 token、建待確認，**不綁定**。

    回「要綁給誰」與（若這支 Telegram 已綁在別的帳號）「目前綁在誰」，bot 拿去顯示
    確認訊息＋[確認][取消]。短碼在這裡只讀不消耗（確認時才消耗）。
    """
    full, code = await run_sync(_resolve_link_token, body.token)
    if not full:
        raise HTTPException(status_code=400, detail=_INVALID_LINK)
    target_user_id, exp_ts, nonce = await _check_link_token(full)

    user = await run_sync(get_user_by_id, target_user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is not active.")

    target_label = await run_sync(_account_label, target_user_id)
    current_label = None
    existing = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if existing and existing.get("user_id") and existing["user_id"] != target_user_id:
        current_label = await run_sync(_account_label, existing["user_id"])

    ttl = max(exp_ts - int(time.time()), 1)
    stored = await run_sync(
        _store_pending, nonce, {"t": full, "code": code, "tg": body.telegram_id}, ttl
    )
    if not stored:
        raise HTTPException(status_code=503, detail="Link confirmation unavailable.")
    return LinkPreviewResponse(
        pending_id=nonce,
        target_label=target_label,
        current_label=current_label,
        expires_in=ttl,
    )


@router.post("/link-cancel")
@limiter.limit("20/minute")
async def link_cancel(
    request: Request,
    body: LinkCancelRequest,
    _: None = Depends(_require_bot_secret),
):
    """[取消]：作廢這筆待確認、它的短碼與 token（之後同一個連結／指令都不能再用）。"""
    pending = await _load_owned_pending(body.pending_id, body.telegram_id)
    await run_sync(_claim_pending, body.pending_id, pending)
    await run_sync(_mark_link_token_consumed, body.pending_id, LINK_TOKEN_TTL_SECONDS)
    return {"success": True}


@router.post("/verify-link", response_model=VerifyLinkResponse)
@limiter.limit("10/minute")
async def verify_link(
    request: Request,
    body: VerifyLinkRequest,
    _: None = Depends(_require_bot_secret),
):
    """[確認]：同一個 Telegram 使用者按下才綁定（見上方「確認步驟」）。

    搶到待確認（一次性）→ 再驗一次 token（等確認的期間可能過期）→ 綁定 → 標記 nonce 已用。
    綁定對象永遠是 token 內 HMAC 簽過的 user_id。
    """
    pending = await _load_owned_pending(body.pending_id, body.telegram_id)
    if not await run_sync(_claim_pending, body.pending_id, pending):
        raise HTTPException(status_code=400, detail=_INVALID_LINK)
    matched_user_id, exp_ts, nonce = await _check_link_token(pending["t"])

    user = await run_sync(get_user_by_id, matched_user_id)
    if not user or not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is not active.")

    # 不因 telegram_id 已綁定而拒絕（移除舊的 409）。create_telegram_binding 會以
    # telegram_id upsert 方式把綁定「移動」到本次 token 對應的帳號（= 重新綁定 /
    # 切換帳號），並清掉該帳號舊的 telegram 綁定。安全性：必須同時握有「該 Telegram」
    # （由 bot 端證明，使用者只能用自己的 Telegram 按確認）與「目標帳號的有效
    # token」（由該帳號 web session 產生）；而 token 可能是別人丟過來的連結，
    # 所以確認訊息會講明要綁給誰、以及是否從別的帳號移過來（link-preview）。
    # 這也修復了：舊綁定殘留 → 網頁顯示未綁定卻無法重綁的卡死狀態。
    existing = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if existing and existing.get("user_id") != matched_user_id:
        logger.info(
            "Telegram re-bind: telegram_id=%s moving from user=%s to user=%s",
            body.telegram_id,
            existing.get("user_id"),
            matched_user_id,
        )

    ok = await run_sync(
        create_telegram_binding,
        body.telegram_id,
        matched_user_id,
        body.username,
        body.first_name,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to create binding.")

    # 標記 token 已消費：同一個連結／手動指令都不能再預覽或確認。
    await run_sync(_mark_link_token_consumed, nonce, exp_ts - int(time.time()))

    logger.info(
        "Telegram binding created: telegram_id=%s user_id=%s",
        body.telegram_id,
        matched_user_id,
    )
    brief_on, brief_hour = await run_sync(_brief_after_bind, matched_user_id)
    return VerifyLinkResponse(
        success=True,
        user_id=matched_user_id,
        username=user["username"],
        brief_enabled=brief_on,
        brief_hour=brief_hour,
    )


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("10/minute")
async def bot_chat(
    request: Request,
    body: ChatRequest,
    _: None = Depends(_require_bot_secret),
):
    """Bot-facing chat endpoint.

    Resolves the Telegram binding, loads the user's BYOK api key,
    and runs the ManagerAgent graph directly. Returns the final
    response text (no SSE — the bot handles user-facing delivery).
    """
    # Lazy import to keep module import side-effects minimal.
    from api.routers.telegram_chat import run_telegram_chat

    try:
        return await run_telegram_chat(
            telegram_id=body.telegram_id,
            message=body.message,
            language=body.language,
            image_data_url=body.image_data_url,
        )
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.exception("Telegram chat failed: %s", exc)
        raise HTTPException(status_code=500, detail="Chat processing failed.") from exc


@router.post("/chat/resume", response_model=ChatResponse)
@limiter.limit("20/minute")
async def bot_chat_resume(
    request: Request,
    body: ResumeRequest,
    _: None = Depends(_require_bot_secret),
):
    """回答 pending 的 HITL 卡（按鈕 callback），繼續跑 graph。

    409 = 這個 session 沒有等待中的卡（重複點、或 API 重啟後 checkpointer
    已清空）；bot 端提示使用者重打一次即可。
    """
    from api.routers.telegram_chat import run_telegram_resume

    try:
        return await run_telegram_resume(
            telegram_id=body.telegram_id,
            decision=body.decision,
            option_index=body.option_index,
            text=body.text,
        )
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.exception("Telegram chat resume failed: %s", exc)
        raise HTTPException(status_code=500, detail="Chat processing failed.") from exc


def _tg_default_session_id(telegram_id: int) -> str:
    return f"tg:{telegram_id}"


@router.post("/sessions", response_model=SessionsResponse)
@limiter.limit("20/minute")
async def bot_list_sessions(
    request: Request,
    body: SessionsRequest,
    _: None = Depends(_require_bot_secret),
):
    """Bot-facing: list the linked user's chat sessions for /sessions."""
    binding = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if not binding:
        raise HTTPException(status_code=403, detail="NOT_BOUND")

    # ✅ 標記要跟「bot 實際會用的 session」一致：優先跨平台 current_session_id，
    # 其次 Telegram 釘選的 active_session_id，最後預設 —— 與 telegram_chat 的
    # 取用順序相同，避免清單標的對話與實際接續的對話不符。
    active = (
        await run_sync(get_current_session, binding["user_id"])
        or await run_sync(get_telegram_active_session, body.telegram_id)
        or _tg_default_session_id(body.telegram_id)
    )
    rows = await run_sync(get_sessions, binding["user_id"], 20, 0)
    sessions = [
        SessionItem(
            id=row["id"],
            title=row.get("title") or "New Chat",
            updated_at=row.get("updated_at"),
            is_active=row["id"] == active,
        )
        for row in rows
    ]
    return SessionsResponse(success=True, sessions=sessions, active_session_id=active)


@router.post("/brief")
@limiter.limit("20/minute")
async def bot_set_brief(
    request: Request,
    body: BriefToggleRequest,
    _: None = Depends(_require_bot_secret),
):
    """Bot-facing: ``/brief on|off``——切每日早報開關（docs/plans/2026-09-12-daily-brief-retention-design.md）。"""
    from core.daily_brief import store as brief_store
    from core.daily_brief.schedule import default_timezone_for_language
    from core.database.user import get_user_language

    binding = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if not binding:
        raise HTTPException(status_code=403, detail="NOT_BOUND")
    language = await run_sync(get_user_language, binding["user_id"])
    await run_sync(
        lambda: brief_store.set_enabled(
            binding["user_id"],
            body.enabled,
            timezone=default_timezone_for_language(language),
        )
    )
    return {"success": True, "enabled": body.enabled}


@router.post("/language")
@limiter.limit("20/minute")
async def bot_set_language(
    request: Request,
    body: LanguageSetRequest,
    _: None = Depends(_require_bot_secret),
):
    """Bot-facing: ``/lang``——改帳號語言（2026-09-28）。

    帳號語言＝早報、通知、以及看不出語言的對話訊息用的語言；跟網站右上角切語言是同一個設定。
    """
    from core.database.user import set_user_language
    from core.i18n import SUPPORTED_LANGUAGES

    if body.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_LANGUAGE")
    binding = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if not binding:
        raise HTTPException(status_code=403, detail="NOT_BOUND")
    if not await run_sync(set_user_language, binding["user_id"], body.language):
        raise HTTPException(status_code=500, detail="unknown_error")
    return {"success": True, "language": body.language}


@router.post("/use-session", response_model=UseSessionResponse)
@limiter.limit("20/minute")
async def bot_use_session(
    request: Request,
    body: UseSessionRequest,
    _: None = Depends(_require_bot_secret),
):
    """Bot-facing: switch the active session for /sessions selection.

    Passing an empty/None session_id resets to the default rolling
    ``tg:{telegram_id}`` session.
    """
    binding = await run_sync(get_binding_by_telegram_id, body.telegram_id)
    if not binding:
        raise HTTPException(status_code=403, detail="NOT_BOUND")

    target = (body.session_id or "").strip() or None
    default_id = _tg_default_session_id(body.telegram_id)

    # 切換到非預設 session 時，驗證該 session 確實屬於此使用者，
    # 避免透過 bot 讀取他人對話。
    if target and target != default_id:
        owns = await run_sync(check_session_ownership, target, binding["user_id"])
        if not owns:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")

    # 預設 session 以 NULL 儲存（fallback 行為），其餘存實際 id。
    stored = None if (target is None or target == default_id) else target
    ok = await run_sync(set_telegram_active_session, body.telegram_id, stored)
    if not ok:
        raise HTTPException(status_code=500, detail="UPDATE_FAILED")

    # 同步跨平台共用的當前對話 → 在 Telegram /sessions 選的對話，網頁端也跟著。
    await run_sync(set_current_session, binding["user_id"], stored or default_id)

    return UseSessionResponse(success=True, active_session_id=stored or default_id)
