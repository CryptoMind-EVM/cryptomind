"""寄信服務：與服務商無關的介面＋Resend REST 實作（httpx 已是依賴；不加 SDK）。

fail closed：旗標開但任何必要 env 缺（或 production 的 base URL 不是 https）→ ``get_sender()``
回 None，一封都不寄；缺哪些只在每個行程 warning 一次，而且只列名稱、不印值。

收件地址是個資：log 一律走 ``mask_email``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from email.utils import parseaddr
from typing import Mapping, Optional, Protocol

import httpx

from core.feature_flags import email_brief_enabled

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
SUPPORTED_PROVIDERS = ("resend",)
MIN_SECRET_LEN = 32

# 已警告過的缺漏組合（每個行程只講一次，cron 每小時跑也不會洗版）
_warned: set = set()


def _reset_warnings() -> None:
    """測試用：清掉「已警告過」的紀錄。"""
    _warned.clear()


def mask_email(email: object) -> str:
    """log 用：``danny@gmail.com`` → ``d***@g***.com``（網域只留最後一段）。"""
    if not isinstance(email, str) or email.count("@") != 1:
        return "***"
    local, domain = email.split("@")
    if not local or not domain:
        return "***"
    tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return f"{local[0]}***@{domain[0]}***" + (f".{tld}" if tld else "")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    html: str
    text: str
    headers: Mapping[str, str] = field(default_factory=dict)
    # 服務商端去重（同一封早報重試或 cron 重跑不會寄兩次）
    idempotency_key: Optional[str] = None


class EmailSender(Protocol):
    async def send(self, message: OutgoingEmail) -> bool: ...


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in {"production", "prod"}


def provider_name() -> str:
    return (_env("EMAIL_PROVIDER") or "resend").lower()


def token_secret() -> str:
    """退訂 token 的 HMAC 金鑰：``EMAIL_TOKEN_SECRET`` 優先，沒設沿用 ``JWT_SECRET_KEY``。"""
    return _env("EMAIL_TOKEN_SECRET") or _env("JWT_SECRET_KEY")


def public_base() -> str:
    """信裡連結的站台網址：PUBLIC_BASE_URL → SITE_URL（api/public_base.py）。"""
    from api.public_base import public_base_url

    return public_base_url()


def sender_name() -> str:
    return parseaddr(_env("EMAIL_FROM"))[0] or "CryptoMind"


def postal_address() -> str:
    return _env("EMAIL_SENDER_POSTAL_ADDRESS")


def missing_config() -> list:
    """缺哪些設定（只列名稱）。空清單＝可以寄。"""
    missing = []
    name = provider_name()
    if name not in SUPPORTED_PROVIDERS:
        missing.append("EMAIL_PROVIDER")
    elif name == "resend" and not _env("RESEND_API_KEY"):
        missing.append("RESEND_API_KEY")
    if "@" not in parseaddr(_env("EMAIL_FROM"))[1]:
        missing.append("EMAIL_FROM")
    if not postal_address():
        missing.append("EMAIL_SENDER_POSTAL_ADDRESS")
    base = public_base()
    base_ok = base.startswith("https://") or (
        base.startswith("http://") and not _is_production()
    )
    if not base_ok:
        missing.append("PUBLIC_BASE_URL")
    if len(token_secret()) < MIN_SECRET_LEN:
        missing.append("EMAIL_TOKEN_SECRET")
    return missing


def email_brief_available() -> bool:
    """給 /api/config：旗標開而且設定齊全（不寫 log）。UI 只在這時候顯示 Email 區塊。"""
    return email_brief_enabled() and not missing_config()


def get_sender() -> Optional[EmailSender]:
    """旗標關或設定不全 → None（呼叫端一律視為「不寄」）。"""
    if not email_brief_enabled():
        return None
    missing = missing_config()
    if missing:
        key = ",".join(missing)
        if key not in _warned:
            _warned.add(key)
            logger.warning(
                "[email_brief] EMAIL_BRIEF_ENABLED is on but config is incomplete; "
                "no email will be sent. missing=%s",
                key,
            )
        return None
    return ResendSender(_env("RESEND_API_KEY"), _env("EMAIL_FROM"))


class ResendSender:
    """``POST https://api.resend.com/emails``（Bearer）。任何錯誤都回 False，不往外拋。

    重試：429／5xx／網路錯誤最多再試一次（逾時 10 秒）；4xx 是我們的請求有問題，重試沒用。
    """

    def __init__(
        self,
        api_key: str,
        from_address: str,
        *,
        timeout: float = 10.0,
        max_attempts: int = 2,
        backoff: float = 0.5,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self._from = from_address
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._backoff = backoff
        self._transport = transport

    async def send(self, message: OutgoingEmail) -> bool:
        payload = {
            "from": self._from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
            "text": message.text,
        }
        if message.headers:
            payload["headers"] = dict(message.headers)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key
        who = mask_email(message.to)
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    resp = await client.post(
                        RESEND_API_URL, json=payload, headers=headers
                    )
                if 200 <= resp.status_code < 300:
                    return True
                # 回應內容可能回顯收件地址，只記狀態碼
                retryable = resp.status_code == 429 or resp.status_code >= 500
                logger.warning(
                    "[email_brief] resend rejected to=%s status=%s attempt=%d",
                    who,
                    resp.status_code,
                    attempt,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except httpx.HTTPError as exc:
                retryable = True
                logger.warning(
                    "[email_brief] resend error to=%s attempt=%d: %s",
                    who,
                    attempt,
                    type(exc).__name__,
                )
            if not retryable or attempt >= self._max_attempts:
                return False
            await asyncio.sleep(self._backoff * attempt)
        return False
