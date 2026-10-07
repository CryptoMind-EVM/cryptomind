"""Telegram Bot Service — aiogram3-based bot that calls the CryptoMind API."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Optional

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)

from bot.i18n import DEFAULT_LANGUAGE, MESSAGES, t

__version__ = "1.2.0"

# 附圖上限（與後端 VISION_MAX_IMAGE_BYTES 預設一致）——超過先在 bot 端擋，
# 免得白傳 4MB+ 才收到 400。
_MAX_IMAGE_BYTES = 4 * 1024 * 1024

log = logging.getLogger("bot.telegram_bot")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

_TG_MESSAGE_LIMIT = 4096
# Callback-data prefix for session selection. callback_data is capped at
# 64 bytes; "tgsess:" (7) + a UUID4 (36) fits comfortably. An empty id
# after the prefix means "switch back to the default Telegram session".
_SESSION_CB_PREFIX = "tgsess:"
_SESSION_BTN_MAXLEN = 40  # keep inline button labels readable
# HITL 卡按鈕（2026-09-14）：後端 ChatResponse.hitl.buttons 的 decision 直接
# 塞進 callback_data——"hitl:approve" / "hitl:deny" / "hitl:wrap" / "hitl:opt:3"。
# 不帶 session：後端從 telegram_id 解析當前 session，跟 /chat 一樣。
_HITL_CB_PREFIX = "hitl:"
# /lang（2026-09-28）：改帳號語言（早報、通知、看不出語言的對話用它）
_LANG_CB_PREFIX = "tglang:"
_LANG_CHOICES = (
    ("zh-TW", "繁體中文"),
    ("zh-CN", "简体中文"),
    ("en", "English"),
    ("ru", "Русский"),
)
_LANG_ALIASES = {
    "zh-tw": "zh-TW",
    "tw": "zh-TW",
    "zh-cn": "zh-CN",
    "cn": "zh-CN",
    "en": "en",
    "ru": "ru",
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class BotConfig:
    token: str
    api_base_url: str
    bot_secret: str
    mode: str
    webhook_host: str
    webhook_path: str
    webapp_url: str

    @classmethod
    def from_env(cls) -> "BotConfig":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        # Public HTTPS URL of the TON Mini App. Opening this via a web_app
        # button / menu button is what gives the page a real Telegram context
        # (platform=android/ios, initData populated).
        # Production is self-hosted at getcryptomind.com (the old *.zeabur.app
        # domains are gone). Kept as its own env rather than TON_MANIFEST_URL so
        # the bot and the web manifest can be pointed independently.
        webapp_url = (
            os.getenv("TELEGRAM_WEBAPP_URL") or "https://getcryptomind.com"
        ).rstrip("/")
        return cls(
            token=token,
            api_base_url=os.getenv("API_BASE_URL", "http://localhost:8080").rstrip("/"),
            bot_secret=os.getenv("BOT_INTERNAL_SECRET", ""),
            mode=os.getenv("TELEGRAM_BOT_MODE", "polling").lower(),
            webhook_host=os.getenv("TELEGRAM_WEBHOOK_HOST", "").rstrip("/"),
            webhook_path=os.getenv("TELEGRAM_WEBHOOK_PATH", "/bot/webhook"),
            webapp_url=webapp_url,
        )


# ---------------------------------------------------------------------------
# API Error
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, code: str, status_code: int):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _user_lang(message: Message) -> str:
    """Derive a supported language code from the Telegram user's profile."""
    lc = (message.from_user.language_code or "").lower() if message.from_user else ""
    if lc.startswith("zh"):
        return "zh-TW"
    # Default to English when the language is non-Chinese OR unknown/empty.
    # Telegram Apps Center requires the bot to reply to /start in English by
    # default; only explicit zh clients get Chinese.
    return "en"


def _detail_to_code(exc: httpx.HTTPStatusError) -> str:
    """Extract the API error code from an HTTPStatusError response."""
    try:
        data = exc.response.json()
        detail = data.get("detail", "")
        if isinstance(detail, str) and detail in MESSAGES["zh-TW"]:
            return detail
        return detail if isinstance(detail, str) else str(exc)
    except Exception:
        return str(exc)


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


class ApiClient:
    """Calls CryptoMind bot-facing endpoints with X-Bot-Secret auth."""

    # 150s：複雜分析會跑多輪 LLM(意圖→多工具→彙整→反思→合成)，60s 常不夠用，
    # 會在伺服器仍在運算時就被 bot 端判「回覆時間過長」。設在 gunicorn timeout(180)
    # 之下、留網路餘裕。verify-link/sessions 很快，不受此上限影響。
    def __init__(self, base_url: str, bot_secret: str, timeout: float = 150.0):
        self.base_url = base_url
        self.bot_secret = bot_secret
        self.timeout = timeout

    def _headers(self, telegram_id: Optional[int] = None) -> dict[str, str]:
        if not self.bot_secret:
            return {}
        headers = {"X-Bot-Secret": self.bot_secret}
        # 限流以 Telegram 使用者分開計（API 端驗過 X-Bot-Secret 才採信）——不帶的話
        # 全體 Telegram 使用者共用 bot 容器一個 IP 的配額（2026-09-24）
        if telegram_id is not None:
            headers["X-Telegram-User-Id"] = str(telegram_id)
        return headers

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers=self._headers(payload.get("telegram_id")),
            )
            return resp

    async def _get(self, path: str, telegram_id: Optional[int] = None) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                f"{self.base_url}{path}",
                headers=self._headers(telegram_id),
            )
            return resp

    async def check_address(
        self, address: str, telegram_id: Optional[int] = None
    ) -> dict:
        """地址健診（公開端點；設計 2026-08-18）。422 → INVALID_ADDRESS。"""
        from urllib.parse import quote

        resp = await self._get(
            "/api/scam-tracker/reports/check?address=" + quote(address, safe=""),
            telegram_id,
        )
        if resp.status_code == 422:
            raise ApiError("INVALID_ADDRESS", 422)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _raise_link_error(resp) -> None:
        """綁定三支端點共用的錯誤對應；403 用 detail 分辨「不是你的確認」與帳號停用。"""
        if resp.status_code in (400, 422):
            raise ApiError("INVALID_TOKEN", resp.status_code)
        if resp.status_code == 403:
            try:
                detail = resp.json().get("detail")
            except Exception:  # noqa: BLE001 — 讀不到 body 就當帳號問題
                detail = None
            code = "LINK_NOT_YOURS" if detail == "LINK_NOT_YOURS" else "USER_NOT_ACTIVE"
            raise ApiError(code, 403)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()

    async def link_preview(self, token: str, telegram_id: int) -> dict:
        """/start link_<code> 或 /link <token>：只建待確認，不綁定。"""
        resp = await self._post(
            "/api/telegram/link-preview",
            {"token": token, "telegram_id": telegram_id},
        )
        self._raise_link_error(resp)
        return resp.json()

    async def confirm_link(
        self,
        pending_id: str,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
    ) -> dict:
        """[確認]：同一個 Telegram 使用者按下才真的綁定。"""
        resp = await self._post(
            "/api/telegram/verify-link",
            {
                "pending_id": pending_id,
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
            },
        )
        self._raise_link_error(resp)
        return resp.json()

    async def cancel_link(self, pending_id: str, telegram_id: int) -> dict:
        resp = await self._post(
            "/api/telegram/link-cancel",
            {"pending_id": pending_id, "telegram_id": telegram_id},
        )
        self._raise_link_error(resp)
        return resp.json()

    async def chat(
        self,
        telegram_id: int,
        message: str,
        language: str = "zh-TW",
        image_data_url: Optional[str] = None,
    ) -> dict:
        payload: dict = {
            "telegram_id": telegram_id,
            "message": message,
            "language": language,
        }
        if image_data_url:
            payload["image_data_url"] = image_data_url
        resp = await self._post("/api/telegram/chat", payload)
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()
        return resp.json()

    async def resume(
        self,
        telegram_id: int,
        decision: str,
        option_index: Optional[int] = None,
        text: Optional[str] = None,
    ) -> dict:
        """回答 pending 的 HITL 卡。409 = 卡已過期（NO_PENDING_HITL）。"""
        payload: dict = {"telegram_id": telegram_id, "decision": decision}
        if option_index is not None:
            payload["option_index"] = option_index
        if text:
            payload["text"] = text
        resp = await self._post("/api/telegram/chat/resume", payload)
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code == 409:
            raise ApiError("NO_PENDING_HITL", 409)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()
        return resp.json()

    async def list_sessions(self, telegram_id: int) -> dict:
        resp = await self._post("/api/telegram/sessions", {"telegram_id": telegram_id})
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()
        return resp.json()

    async def set_brief(self, telegram_id: int, enabled: bool) -> dict:
        resp = await self._post(
            "/api/telegram/brief", {"telegram_id": telegram_id, "enabled": enabled}
        )
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code != 200:
            raise ApiError("unknown_error", resp.status_code)
        return resp.json()

    async def set_language(self, telegram_id: int, language: str) -> dict:
        resp = await self._post(
            "/api/telegram/language", {"telegram_id": telegram_id, "language": language}
        )
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code != 200:
            raise ApiError("unknown_error", resp.status_code)
        return resp.json()

    async def use_session(self, telegram_id: int, session_id: Optional[str]) -> dict:
        resp = await self._post(
            "/api/telegram/use-session",
            {"telegram_id": telegram_id, "session_id": session_id},
        )
        if resp.status_code == 403:
            raise ApiError("NOT_BOUND", 403)
        if resp.status_code == 404:
            raise ApiError("SESSION_NOT_FOUND", 404)
        if resp.status_code == 429:
            raise ApiError("RATE_LIMITED", 429)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_chunks(text: str) -> list[str]:
    """Split text into Telegram-sized chunks, preferring line boundaries."""
    if len(text) <= _TG_MESSAGE_LIMIT:
        return [text]
    chunks: list[str] = []
    chunk = ""
    for line in text.split("\n"):
        test = (chunk + "\n" + line).strip() if chunk else line
        if len(test) <= _TG_MESSAGE_LIMIT:
            chunk = test
            continue
        if chunk:
            chunks.append(chunk)
            chunk = ""
        while len(line) > _TG_MESSAGE_LIMIT:
            chunks.append(line[:_TG_MESSAGE_LIMIT])
            line = line[_TG_MESSAGE_LIMIT:]
        chunk = line
    if chunk:
        chunks.append(chunk)
    return chunks


async def _replace_placeholder(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    """Replace the "analyzing" placeholder with the real answer.

    The edit is best-effort: if Telegram rejects it (rate limit, message
    deleted, or text over the 4096 limit) we still send the answer instead
    of leaving the user staring at "分析中..." forever.

    ``reply_markup``（HITL 卡按鈕）掛在最後一段——按鈕要貼著問題。
    """
    chunks = _split_chunks(text)
    last = len(chunks) - 1
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=chunks[0],
            reply_markup=reply_markup if last == 0 else None,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("edit placeholder failed, sending instead: %s", exc)
        await bot.send_message(
            chat_id=chat_id,
            text=chunks[0],
            reply_markup=reply_markup if last == 0 else None,
        )
    for i, chunk in enumerate(chunks[1:], start=1):
        await asyncio.sleep(0.1)
        await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            reply_markup=reply_markup if i == last else None,
        )


def _hitl_keyboard(hitl: Optional[dict]) -> Optional[InlineKeyboardMarkup]:
    """ChatResponse.hitl.buttons → inline 鍵盤。同意／取消並排一列，選項各一列。"""
    if not isinstance(hitl, dict):
        return None
    buttons = [b for b in hitl.get("buttons") or [] if isinstance(b, dict)]
    if not buttons:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for b in buttons:
        decision = str(b.get("decision") or "")
        label = str(b.get("label") or decision)[:_SESSION_BTN_MAXLEN]
        if decision == "option":
            data = f"{_HITL_CB_PREFIX}opt:{int(b.get('index', 0))}"
            rows.append([InlineKeyboardButton(text=label, callback_data=data)])
            continue
        pair.append(
            InlineKeyboardButton(text=label, callback_data=_HITL_CB_PREFIX + decision)
        )
    if pair:
        rows.insert(0, pair)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _parse_hitl_callback(data: str) -> tuple[str, Optional[int]]:
    """``hitl:opt:2`` → ("option", 2)；``hitl:approve`` → ("approve", None)。"""
    body = data[len(_HITL_CB_PREFIX) :]
    if body.startswith("opt:"):
        try:
            return "option", int(body[4:])
        except ValueError:
            return "deny", None
    return (body if body in ("approve", "deny", "wrap") else "deny"), None


async def _deliver(
    bot: Bot, chat_id: int, placeholder_id: int, result: dict, lang: str
) -> None:
    """把 /chat 或 /chat/resume 的結果落到佔位訊息上：HITL 卡帶按鈕，否則純文字。"""
    hitl = result.get("hitl")
    reply = result.get("response")
    if not reply:
        reply = t("empty_response", lang)
    elif not hitl:
        reply = f"{reply}\n\n{t('disclaimer', lang)}"
    await _replace_placeholder(
        bot, chat_id, placeholder_id, reply, reply_markup=_hitl_keyboard(hitl)
    )


async def _keep_typing(bot: Bot, chat_id: int) -> None:
    """Hold the "typing..." indicator while the backend works.

    Telegram clears the indicator after ~5s, and a chat answer takes 15-30s,
    so a one-shot send_chat_action leaves the chat looking frozen.
    """
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("keep-typing stopped: %s", exc)


_START_LINK_PREFIX = "link_"


def _start_link_code(text: str) -> Optional[str]:
    """``/start link_<code>``（網站一鍵綁定的 deep link）→ ``<code>``；其他 /start 回 None。"""
    parts = (text or "").strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[0].startswith("/start"):
        return None
    payload = parts[1].strip()
    if not payload.startswith(_START_LINK_PREFIX):
        return None
    return payload[len(_START_LINK_PREFIX) :] or None


# 綁定確認鈕（2026-09-27 安全需求）：callback_data＝"tgl:ok:<pending_id>"／"tgl:no:<pending_id>"，
# pending_id 是 16 hex（23 bytes，在 64 bytes 上限內），不放完整 token。
_LINK_CB_PREFIX = "tgl:"
_LINK_PENDING_RE = re.compile(r"^[0-9a-f]{16}$")


def _parse_link_callback(data: str) -> Optional[tuple[str, str]]:
    """``tgl:ok:<id>`` → ("ok", id)；``tgl:no:<id>`` → ("no", id)；其他回 None。"""
    if not (data or "").startswith(_LINK_CB_PREFIX):
        return None
    action, _, pending_id = data[len(_LINK_CB_PREFIX) :].partition(":")
    if action not in ("ok", "no") or not _LINK_PENDING_RE.fullmatch(pending_id):
        return None
    return action, pending_id


def _link_confirm_text(preview: dict, lang: str) -> str:
    target = preview.get("target_label") or "?"
    parts = [t("link_confirm_prompt", lang, target=target)]
    if preview.get("current_label"):
        parts.append(
            t(
                "link_confirm_move",
                lang,
                current=preview["current_label"],
                target=target,
            )
        )
    minutes = max(1, round(int(preview.get("expires_in") or 300) / 60))
    parts.append(t("link_confirm_warning", lang, minutes=minutes))
    return "\n\n".join(parts)


def _link_confirm_keyboard(pending_id: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("link_confirm_btn", lang),
                    callback_data=f"{_LINK_CB_PREFIX}ok:{pending_id}",
                ),
                InlineKeyboardButton(
                    text=t("link_cancel_btn", lang),
                    callback_data=f"{_LINK_CB_PREFIX}no:{pending_id}",
                ),
            ]
        ]
    )


def _link_success_text(result: dict, lang: str) -> str:
    text = t("link_success", lang, username=result.get("username", "user"))
    hour = result.get("brief_hour")
    if result.get("brief_enabled") and isinstance(hour, int):
        text += "\n\n" + t("link_brief_on", lang, hour=f"{hour:02d}:00")
    elif "brief_enabled" in result:
        text += "\n\n" + t("link_brief_off", lang)
    return text


async def _link_account(
    api: "ApiClient", message: Message, token: str, lang: str
) -> None:
    """/link <token> 與 /start link_<code> 共用：**不綁定**，只顯示要綁給誰＋[確認][取消]。

    連結可能是別人丟過來的（綁到對方帳號後，對方能在網頁讀到這裡的對話），
    所以一定要本人看過帳號再按確認（on_link_button）。
    """
    user = message.from_user
    try:
        preview = await api.link_preview(token=token, telegram_id=user.id)
        await message.answer(
            _link_confirm_text(preview, lang),
            reply_markup=_link_confirm_keyboard(preview["pending_id"], lang),
        )
    except ApiError as exc:
        await message.answer(t("link_failed", lang, detail=t(exc.code, lang)))
    except httpx.TimeoutException:
        await message.answer(t("link_timeout", lang))
    except httpx.HTTPStatusError as exc:
        code = _detail_to_code(exc)
        detail = t(code, lang) if code in MESSAGES[lang] else code
        await message.answer(t("link_failed", lang, detail=detail))
    except Exception:
        log.exception("Link preview error telegram_id=%s", user.id)
        await message.answer(t("unknown_error", lang))


async def _confirm_link_reply(
    api: "ApiClient", action: str, pending_id: str, user, lang: str
) -> tuple[str, bool]:
    """按下 [確認]／[取消] → (回覆文字, 是否收掉按鈕)。

    用「按按鈕的人」的 telegram_id 打 API，伺服器比對是不是開啟連結的同一人；
    別人按到（群組）回 LINK_NOT_YOURS，按鈕留給本人。逾時／未知錯誤也留著可重按。
    """
    try:
        if action == "no":
            await api.cancel_link(pending_id=pending_id, telegram_id=user.id)
            return t("link_cancelled", lang), True
        result = await api.confirm_link(
            pending_id=pending_id,
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        return _link_success_text(result, lang), True
    except ApiError as exc:
        if exc.code == "LINK_NOT_YOURS":
            return t("LINK_NOT_YOURS", lang), False
        return t("link_failed", lang, detail=t(exc.code, lang)), True
    except httpx.TimeoutException:
        return t("link_timeout", lang), False
    except httpx.HTTPStatusError as exc:
        code = _detail_to_code(exc)
        detail = t(code, lang) if code in MESSAGES[lang] else code
        return t("link_failed", lang, detail=detail), True
    except Exception:
        log.exception("Link confirm error telegram_id=%s", user.id)
        return t("unknown_error", lang), False


def _build_sessions_keyboard(data: dict, lang: str) -> InlineKeyboardMarkup:
    """Render the session list as one inline button per row.

    Always includes a row for the default Telegram session so users can
    return to it without /new.
    """
    rows: list[list[InlineKeyboardButton]] = []
    active_id = data.get("active_session_id")

    for s in data.get("sessions", []):
        # 預設 tg session 由下方獨立按鈕呈現，避免重複。
        if str(s.get("id", "")).startswith("tg:"):
            continue
        title = (s.get("title") or "New Chat").strip()
        if len(title) > _SESSION_BTN_MAXLEN:
            title = title[: _SESSION_BTN_MAXLEN - 1] + "…"
        label = ("✅ " if s.get("is_active") else "💬 ") + title
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=_SESSION_CB_PREFIX + str(s["id"]),
                )
            ]
        )

    default_active = not active_id or str(active_id).startswith("tg:")
    default_label = ("✅ " if default_active else "") + t(
        "sessions_default_label", lang
    )
    rows.append(
        [InlineKeyboardButton(text=default_label, callback_data=_SESSION_CB_PREFIX)]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def create_handlers(api: ApiClient, webapp_url: str) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def cmd_start(message: Message) -> None:
        lang = _user_lang(message)
        # 網站「連結 Telegram」的 deep link：t.me/<bot>?start=link_<code> → 直接綁定，
        # 不用手動貼 /link（PR-7 2026-09-27）
        code = _start_link_code(message.text or "")
        if code:
            await _link_account(api, message, code, lang)
            return
        # A web_app button launches the page AS a Mini App (real platform +
        # initData), unlike a plain URL button which only opens the in-app
        # browser (platform=unknown, no initData → login falls back to wallet).
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t("open_app", lang),
                        web_app=WebAppInfo(url=webapp_url),
                    )
                ]
            ]
        )
        await message.answer(t("welcome", lang), reply_markup=keyboard)

    @router.message(Command("check"))
    async def cmd_check(message: Message) -> None:
        """地址健診：/check <地址> → 多源風險判定（design 2026-08-18）。"""
        lang = _user_lang(message)
        raw = (message.text or "").strip()
        parts = raw.split(maxsplit=1)
        address = parts[1].strip() if len(parts) > 1 else ""
        if not address:
            await message.answer(t("check_usage", lang))
            return
        verdict_emoji = {
            "high_risk": "🚨",
            "caution": "⚠️",
            "no_red_flags": "✅",
        }
        verdict_key = {
            "high_risk": "check_high_risk",
            "caution": "check_caution",
            "no_red_flags": "check_no_red_flags",
        }
        try:
            data = await api.check_address(
                address, message.from_user.id if message.from_user else None
            )
        except ApiError as exc:
            if exc.code == "INVALID_ADDRESS":
                await message.answer(t("check_invalid_address", lang))
            elif exc.code == "RATE_LIMITED":
                await message.answer(t("check_rate_limited", lang))
            else:
                await message.answer(t("check_failed", lang))
            return
        except Exception:
            await message.answer(t("check_failed", lang))
            return
        v = data.get("verdict", "caution")
        lines = [
            f"{verdict_emoji.get(v, '⚠️')} {t(verdict_key.get(v, 'check_caution'), lang)}",
            f"`{address[:16]}…{address[-8:]}` ({data.get('family', '?').upper()})",
        ]
        reasons = data.get("reasons") or []
        if reasons:
            lines.append("")
            lines.extend(f"• {r}" for r in reasons[:6])
        lines.append("")
        lines.append(t("check_footer", lang))
        await message.answer("\n".join(lines))

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(t("help", _user_lang(message)))

    @router.message(Command("link"))
    async def cmd_link(message: Message) -> None:
        lang = _user_lang(message)
        raw = message.text or ""
        token = (
            raw.strip().split(maxsplit=1)[1].strip()
            if len(raw.strip().split(maxsplit=1)) > 1
            else ""
        )
        if not token:
            await message.answer(t("link_missing_token", lang))
            return
        await _link_account(api, message, token, lang)

    @router.message(Command("unlink"))
    async def cmd_unlink(message: Message) -> None:
        await message.answer(t("unlink_redirect", _user_lang(message)))

    @router.message(Command("sessions"))
    async def cmd_sessions(message: Message) -> None:
        lang = _user_lang(message)
        try:
            data = await api.list_sessions(message.from_user.id)
        except ApiError as exc:
            await message.answer(t(exc.code, lang))
            return
        except Exception:
            log.exception("List sessions error telegram_id=%s", message.from_user.id)
            await message.answer(t("sessions_failed", lang))
            return

        non_tg = [
            s
            for s in data.get("sessions", [])
            if not str(s.get("id", "")).startswith("tg:")
        ]
        if not non_tg:
            # 沒有 Web 對話可選，只回提示（仍可用 /new 重置）。
            await message.answer(t("sessions_empty", lang))
            return

        await message.answer(
            t("sessions_header", lang),
            reply_markup=_build_sessions_keyboard(data, lang),
        )

    @router.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        lang = _user_lang(message)
        try:
            await api.use_session(message.from_user.id, None)
            await message.answer(t("session_new_done", lang))
        except ApiError as exc:
            await message.answer(t(exc.code, lang))
        except Exception:
            log.exception("New session error telegram_id=%s", message.from_user.id)
            await message.answer(t("unknown_error", lang))

    @router.callback_query(F.data.startswith(_SESSION_CB_PREFIX))
    async def on_session_pick(callback: CallbackQuery) -> None:
        lang = _user_lang(callback.message) if callback.message else DEFAULT_LANGUAGE
        session_id = callback.data[len(_SESSION_CB_PREFIX) :] or None
        try:
            await api.use_session(callback.from_user.id, session_id)
        except ApiError as exc:
            await callback.answer()
            if callback.message:
                await callback.message.answer(t(exc.code, lang))
            return
        except Exception:
            log.exception("Pick session error telegram_id=%s", callback.from_user.id)
            await callback.answer()
            if callback.message:
                await callback.message.answer(t("unknown_error", lang))
            return

        await callback.answer()
        if not session_id:
            text = t("session_new_done", lang)
        else:
            # 用按鈕上的標題回饋（去掉 ✅/💬 前綴）給使用者確認。
            title = session_id
            for row in (
                callback.message.reply_markup.inline_keyboard
                if callback.message and callback.message.reply_markup
                else []
            ):
                for btn in row:
                    if btn.callback_data == callback.data:
                        title = btn.text.lstrip("✅💬 ").strip()
            text = t("session_switched", lang, title=title)
        if callback.message:
            await callback.message.answer(text)

    async def _apply_language(telegram_id: int, code: str, ui_lang: str) -> str:
        """改帳號語言，回給使用者的確認訊息（用 Telegram 客戶端語言寫）。"""
        try:
            await api.set_language(telegram_id, code)
        except ApiError as exc:
            return t(exc.code, ui_lang)
        except Exception:
            log.exception("Set language error telegram_id=%s", telegram_id)
            return t("unknown_error", ui_lang)
        name = dict(_LANG_CHOICES).get(code, code)
        return t("lang_set", ui_lang, name=name)

    @router.message(Command("lang"))
    async def cmd_lang(message: Message) -> None:
        """/lang [zh-TW|zh-CN|en|ru]：改帳號語言；不帶參數就給四個按鈕選。"""
        lang = _user_lang(message)
        parts = (message.text or "").strip().split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        code = _LANG_ALIASES.get(arg)
        if code:
            await message.answer(
                await _apply_language(message.from_user.id, code, lang)
            )
            return
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=name, callback_data=_LANG_CB_PREFIX + value
                    )
                    for value, name in _LANG_CHOICES[:2]
                ],
                [
                    InlineKeyboardButton(
                        text=name, callback_data=_LANG_CB_PREFIX + value
                    )
                    for value, name in _LANG_CHOICES[2:]
                ],
            ]
        )
        await message.answer(t("lang_choose", lang), reply_markup=keyboard)

    @router.callback_query(F.data.startswith(_LANG_CB_PREFIX))
    async def on_lang_pick(callback: CallbackQuery) -> None:
        # callback.message 是 bot 自己發的訊息，語言要看按按鈕的人
        code = (callback.data or "")[len(_LANG_CB_PREFIX) :]
        lc = (
            (callback.from_user.language_code or "").lower()
            if callback.from_user
            else ""
        )
        ui_lang = "zh-TW" if lc.startswith("zh") else "en"
        await callback.answer()
        if code not in dict(_LANG_CHOICES):
            return
        text = await _apply_language(callback.from_user.id, code, ui_lang)
        if callback.message:
            await callback.message.answer(text)

    @router.message(Command("brief"))
    async def cmd_brief(message: Message) -> None:
        """/brief on|off：每日早報開關（2026-09-12 留存核心第 1 項）。"""
        lang = _user_lang(message)
        parts = (message.text or "").strip().split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if arg not in ("on", "off"):
            await message.answer(t("brief_usage", lang))
            return
        try:
            await api.set_brief(message.from_user.id, arg == "on")
            await message.answer(t("brief_on" if arg == "on" else "brief_off", lang))
        except ApiError as exc:
            await message.answer(t(exc.code, lang))
        except Exception:
            log.exception("Brief toggle error telegram_id=%s", message.from_user.id)
            await message.answer(t("unknown_error", lang))

    @router.message(F.text & ~F.text.startswith("/"))
    async def handle_text(message: Message, bot: Bot) -> None:
        lang = _user_lang(message)
        user = message.from_user
        chat_id = message.chat.id
        placeholder = await bot.send_message(chat_id=chat_id, text=t("analyzing", lang))
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        try:
            result = await api.chat(
                telegram_id=user.id,
                message=message.text or "",
                language=lang,
            )
        except ApiError as exc:
            result = {"response": t(exc.code, lang)}
        except httpx.TimeoutException:
            result = {"response": t("timeout", lang)}
        except httpx.HTTPStatusError as exc:
            code = _detail_to_code(exc)
            result = {
                "response": t(code, lang)
                if code in MESSAGES[lang]
                else t("unknown_error", lang)
            }
        except Exception:
            log.exception("Chat error telegram_id=%s", user.id)
            result = {"response": t("unknown_error", lang)}
        finally:
            typing.cancel()
        # Always land the outcome on the placeholder — success or failure. An
        # error used to arrive as a new message, leaving "分析中..." above it.
        await _deliver(bot, chat_id, placeholder.message_id, result, lang)

    @router.callback_query(F.data.startswith(_LINK_CB_PREFIX))
    async def on_link_button(callback: CallbackQuery, bot: Bot) -> None:
        """綁定確認鈕：只有開啟連結的同一個 Telegram 使用者按確認才綁（伺服器比對）。"""
        lang = (
            "zh-TW"
            if (callback.from_user.language_code or "").lower().startswith("zh")
            else "en"
        )
        parsed = _parse_link_callback(callback.data or "")
        if not parsed:
            await callback.answer()
            return
        action, pending_id = parsed
        text, done = await _confirm_link_reply(
            api, action, pending_id, callback.from_user, lang
        )
        if not done:
            # 別人按到／暫時性錯誤：跳提示、按鈕留著
            await callback.answer(text[:200], show_alert=True)
            return
        await callback.answer()
        msg = callback.message
        if msg is None:
            return
        try:  # 收掉按鈕，避免重按
            await bot.edit_message_reply_markup(
                chat_id=msg.chat.id, message_id=msg.message_id, reply_markup=None
            )
        except Exception as exc:  # noqa: BLE001 — 收按鈕失敗不影響結果
            log.warning("strip link keyboard failed: %s", exc)
        await msg.answer(text)

    @router.callback_query(F.data.startswith(_HITL_CB_PREFIX))
    async def on_hitl_button(callback: CallbackQuery, bot: Bot) -> None:
        """HITL 卡按鈕 → /chat/resume → 結果（可能是下一張卡）落到新佔位訊息。"""
        lang = (
            "zh-TW"
            if (callback.from_user.language_code or "").lower().startswith("zh")
            else "en"
        )
        decision, option_index = _parse_hitl_callback(callback.data or "")
        await callback.answer()
        msg = callback.message
        if msg is None:
            return
        chat_id = msg.chat.id
        # 先把按鈕收掉並標記選了什麼——避免重複點；API 重啟後重複點也只會拿到 409。
        picked = ""
        for row in msg.reply_markup.inline_keyboard if msg.reply_markup else []:
            for btn in row:
                if btn.callback_data == callback.data:
                    picked = btn.text
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg.message_id,
                text=(msg.text or "") + (f"\n\n→ {picked}" if picked else ""),
                reply_markup=None,
            )
        except Exception as exc:  # noqa: BLE001 — 收按鈕失敗不影響後續
            log.warning("strip hitl keyboard failed: %s", exc)

        placeholder = await bot.send_message(chat_id=chat_id, text=t("analyzing", lang))
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        try:
            result = await api.resume(
                telegram_id=callback.from_user.id,
                decision=decision,
                option_index=option_index,
            )
        except ApiError as exc:
            result = {"response": t(exc.code, lang)}
        except httpx.TimeoutException:
            result = {"response": t("timeout", lang)}
        except httpx.HTTPStatusError as exc:
            code = _detail_to_code(exc)
            result = {
                "response": t(code, lang)
                if code in MESSAGES[lang]
                else t("unknown_error", lang)
            }
        except Exception:
            log.exception("HITL resume error telegram_id=%s", callback.from_user.id)
            result = {"response": t("unknown_error", lang)}
        finally:
            typing.cancel()
        await _deliver(bot, chat_id, placeholder.message_id, result, lang)

    @router.message(F.photo | F.document | F.voice | F.video)
    async def handle_media(message: Message, bot: Bot) -> None:
        """多媒體訊息分流：圖片走 vision 分析（Tier 1，2026-08-27 設計），
        語音／影片／非圖文件明確回覆暫不支援——靜默丟掉是最差體驗。"""
        lang = _user_lang(message)
        user = message.from_user
        chat_id = message.chat.id

        # ── 取出圖片 bytes ──
        image_bytes: Optional[bytes] = None
        image_mime = "image/jpeg"  # Telegram photo 一律 jpeg
        if message.photo:
            image_bytes = await bot.download(message.photo[-1], destination=None)
        elif message.document:
            mime = message.document.mime_type or ""
            if not mime.startswith("image/"):
                await message.answer(t("media_unsupported", lang))
                return
            image_mime = mime
            image_bytes = await bot.download(message.document, destination=None)
        else:
            await message.answer(t("media_unsupported", lang))
            return

        if not image_bytes:
            await message.answer(t("unknown_error", lang))
            return
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            await message.answer(t("image_too_large", lang))
            return

        image_data_url = (
            f"data:{image_mime};base64," + base64.b64encode(image_bytes).decode()
        )
        prompt = message.caption or t("image_default_prompt", lang)

        placeholder = await bot.send_message(chat_id=chat_id, text=t("analyzing", lang))
        typing = asyncio.create_task(_keep_typing(bot, chat_id))
        try:
            result = await api.chat(
                telegram_id=user.id,
                message=prompt,
                language=lang,
                image_data_url=image_data_url,
            )
        except ApiError as exc:
            result = {
                "response": t(exc.code, lang)
                if exc.code in MESSAGES[lang]
                else t("vision_failed", lang)
            }
        except httpx.HTTPStatusError as exc:
            code = _detail_to_code(exc)
            result = {
                "response": t(code, lang)
                if code in MESSAGES[lang]
                else t("vision_failed", lang)
            }
        except httpx.TimeoutException:
            result = {"response": t("timeout", lang)}
        except Exception:
            log.exception("Image chat error telegram_id=%s", user.id)
            result = {"response": t("vision_failed", lang)}
        finally:
            typing.cancel()
        await _deliver(bot, chat_id, placeholder.message_id, result, lang)

    return router


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    log.info("Starting CryptoMind Telegram Bot v%s", __version__)
    try:
        config = BotConfig.from_env()
    except ValueError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)

    log.info("API base URL : %s", config.api_base_url)
    log.info("Bot mode     : %s", config.mode)

    # 不使用 HTML/Markdown parse mode：訊息與 AI 回覆皆以純文字傳送，
    # 避免內容含 < > & 或 *_ 等字元時 Telegram 回 "can't parse entities"
    # 而整則訊息送不出去（例如 "/link <token>"、"BTC < 60000"、程式碼片段）。
    bot = Bot(token=config.token)
    dp = Dispatcher()
    dp.include_router(
        create_handlers(
            ApiClient(config.api_base_url, config.bot_secret), config.webapp_url
        )
    )

    async def on_startup() -> None:
        # 註冊指令選單，讓使用者在輸入框看到 / 指令清單（zh-TW + en）。
        zh_commands = [
            BotCommand(command="sessions", description="選擇 / 切換對話"),
            BotCommand(command="new", description="開新對話"),
            BotCommand(command="link", description="綁定平台帳號"),
            BotCommand(command="unlink", description="解除帳號綁定"),
            BotCommand(command="lang", description="切換語言"),
            BotCommand(command="help", description="顯示指令說明"),
        ]
        en_commands = [
            BotCommand(command="sessions", description="Pick / switch conversation"),
            BotCommand(command="new", description="Start a new conversation"),
            BotCommand(command="link", description="Bind platform account"),
            BotCommand(command="unlink", description="Unbind account"),
            BotCommand(command="lang", description="Change language"),
            BotCommand(command="help", description="Show command help"),
        ]
        try:
            await bot.set_my_commands(en_commands)  # default
            await bot.set_my_commands(zh_commands, language_code="zh")
        except Exception as exc:  # noqa: BLE001
            log.warning("set_my_commands failed: %s", exc)
        # Set the chat menu button to launch the Mini App. This is the
        # persistent entry point that opens the page with a real Telegram
        # context (platform + initData); a plain URL menu button would only
        # open the in-app browser and break Telegram login.
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="CryptoMind",
                    web_app=WebAppInfo(url=config.webapp_url),
                )
            )
            log.info("Chat menu button set to Web App: %s", config.webapp_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("set_chat_menu_button failed: %s", exc)
        log.info("Bot startup complete — listening for updates")

    async def on_shutdown() -> None:
        log.info("Shutting down bot...")
        await bot.session.close()

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if config.mode == "webhook":
        await dp.start_webhook(
            listen=config.webhook_host,
            port=8443,
            path=config.webhook_path,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
        )
    else:
        await dp.start_polling(bot, on_startup=on_startup, on_shutdown=on_shutdown)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
