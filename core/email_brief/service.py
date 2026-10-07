"""Email 早報的規則層：地址驗證、token、訂閱狀態、送早報。

- 確認 token：``secrets.token_urlsafe(32)``，DB 只存 sha256、用 hash 查；確認後清掉＝單次；48 小時過期。
- 退訂 token：每封早報都要帶一條有效連結，存隨機值的 hash 就組不回連結，所以用
  ``HMAC-SHA256(EMAIL_TOKEN_SECRET 或 JWT_SECRET_KEY, user_id＋email)`` 推導，DB 只存它的 sha256。
  沒有金鑰推不出來；金鑰換掉後下一封會更新 hash（舊信連結失效，設定頁移除永遠有效）。
- 所有「會不會寄」的判斷都先過 ``provider.get_sender()``：旗標關或設定不全就是 None。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from core.email_brief import provider, render, store
from core.email_brief.provider import EmailSender, mask_email

logger = logging.getLogger(__name__)

CONFIRM_TTL_HOURS = 48
CONFIRM_TTL = timedelta(hours=CONFIRM_TTL_HOURS)
# 同一個地址一小時內被「其他帳號」要求確認幾次就擋（本人另有 3/hour 的端點限流）
ADDRESS_REQUESTS_PER_HOUR = 3

EMAIL_MAX_LEN = 254
LOCAL_MAX_LEN = 64
# 只收 ASCII（第一版不支援國際化網域／地址）；fullmatch 避免 `$` 放過結尾換行
_LOCAL_RE = re.compile(
    r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
)
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD_RE = re.compile(r"[a-z]{2,63}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
_UNSUB_CONTEXT = "cryptomind-email-unsubscribe:v1"


class AddressThrottled(Exception):
    """這個地址最近被太多帳號要求寄確認信。"""


def normalize_email(raw: object) -> str:
    """邊界驗證＋正規化（去空白、轉小寫）。不合格丟 ValueError。"""
    if not isinstance(raw, str):
        raise ValueError("invalid email")
    email = raw.strip().lower()
    if not email or len(email) > EMAIL_MAX_LEN or email.count("@") != 1:
        raise ValueError("invalid email")
    local, domain = email.split("@")
    if len(local) > LOCAL_MAX_LEN or not _LOCAL_RE.fullmatch(local):
        raise ValueError("invalid email")
    labels = domain.split(".")
    if (
        len(labels) < 2
        or not all(_LABEL_RE.fullmatch(label) for label in labels)
        or not _TLD_RE.fullmatch(labels[-1])
    ):
        raise ValueError("invalid email")
    return email


def new_confirm_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_token_shaped(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None


def unsubscribe_token(user_id: str, email: str) -> str:
    secret = provider.token_secret()
    if len(secret) < provider.MIN_SECRET_LEN:
        raise RuntimeError("email token secret is not configured")
    mac = hmac.new(
        secret.encode("utf-8"),
        f"{_UNSUB_CONTEXT}:{user_id}:{email}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _expired(sent_at: Any, now: datetime) -> bool:
    sent = _as_utc(sent_at)
    return sent is None or now - sent > CONFIRM_TTL


def subscription_status(
    row: Optional[Mapping[str, Any]], now: Optional[datetime] = None
) -> dict:
    """none／pending／expired／active／unsubscribed（純函式）。"""
    if not row:
        return {"status": "none", "email": None, "confirm_sent_at": None}
    now = now or datetime.now(timezone.utc)
    if row.get("unsubscribed_at"):
        status = "unsubscribed"
    elif row.get("verified_at"):
        status = "active"
    elif _expired(row.get("confirm_sent_at"), now):
        status = "expired"
    else:
        status = "pending"
    sent = _as_utc(row.get("confirm_sent_at"))
    return {
        "status": status,
        "email": row.get("email"),
        "confirm_sent_at": sent.isoformat() if sent else None,
    }


@dataclass(frozen=True)
class Prepared:
    status: str  # pending＝要寄確認信；active＝同一地址已確認，不重寄
    email: str
    confirm_token: Optional[str] = None


def prepare_subscription(user_id: str, email: str) -> Prepared:
    """同步（DB）。``email`` 必須已經過 ``normalize_email``。"""
    existing = store.get_subscription(user_id)
    if (
        existing
        and existing.get("email") == email
        and subscription_status(existing)["status"] == "active"
    ):
        return Prepared("active", email)
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    if store.count_recent_requests_for_email(email, user_id, since) >= (
        ADDRESS_REQUESTS_PER_HOUR
    ):
        logger.warning(
            "[email_brief] address throttled user=%s to=%s", user_id, mask_email(email)
        )
        raise AddressThrottled()
    token = new_confirm_token()
    store.upsert_pending(
        user_id,
        email,
        hash_token(token),
        hash_token(unsubscribe_token(user_id, email)),
    )
    return Prepared("pending", email, token)


async def send_confirmation(
    sender: EmailSender, email: str, token: str, language: Optional[str]
) -> bool:
    url = f"{provider.public_base()}/api/email/confirm?token={token}"
    message = render.confirmation_email(to=email, confirm_url=url, language=language)
    ok = await sender.send(message)
    logger.info("[email_brief] confirmation to=%s sent=%s", mask_email(email), ok)
    return ok


def confirm(token: object) -> tuple:
    """回 (結果, 語言)：confirmed／expired／invalid。"""
    from core.daily_brief.schedule import (
        DEFAULT_CHANNELS,
        default_timezone_for_language,
    )

    if not is_token_shaped(token):
        return ("invalid", None)
    confirm_hash = hash_token(str(token))
    row = store.find_by_confirm_hash(confirm_hash)
    if not row:
        return ("invalid", None)
    language = row.get("language")
    if _expired(row.get("confirm_sent_at"), datetime.now(timezone.utc)):
        return ("expired", language)
    ok = store.mark_verified(
        row["user_id"],
        confirm_hash,
        default_timezone_for_language(language),
        list(DEFAULT_CHANNELS),
    )
    if not ok:
        return ("invalid", language)
    logger.info(
        "[email_brief] confirmed user=%s to=%s",
        row["user_id"],
        mask_email(row.get("email")),
    )
    return ("confirmed", language)


def unsubscribe(token: object) -> tuple:
    """回 (結果, 語言)：unsubscribed／invalid。冪等，不需要登入。"""
    if not is_token_shaped(token):
        return ("invalid", None)
    row = store.unsubscribe_by_hash(hash_token(str(token)))
    if not row:
        return ("invalid", None)
    logger.info("[email_brief] unsubscribed user=%s", row.get("user_id"))
    return ("unsubscribed", row.get("language"))


def annotate_rows(rows: list) -> list:
    """cron 用：替候選人標 ``email_active``。旗標關或設定不全 → 原樣回傳、不查表（零行為改變）。"""
    if not rows or provider.get_sender() is None:
        return rows
    try:
        active = store.active_user_ids()
    except Exception as exc:  # noqa: BLE001 — 查不到就當沒有 email 頻道，其他管道照送
        logger.warning(
            "[email_brief] load active subscriptions failed: %s", type(exc).__name__
        )
        return rows
    return [{**row, "email_active": str(row.get("user_id")) in active} for row in rows]


async def send_brief_email(
    user_id: str, language: Optional[str], text: str, *, on_date: str
) -> bool:
    """早報的 email 頻道。沒寄出回 False；任何錯誤都不往 cron 拋（Telegram／站內照常）。"""
    sender = provider.get_sender()
    if sender is None:
        return False
    try:
        sub = store.get_active(user_id)
        if not sub:
            return False
        email = sub["email"]
        token = unsubscribe_token(user_id, email)
        token_hash = hash_token(token)
        if not hmac.compare_digest(
            token_hash, str(sub.get("unsubscribe_token_hash") or "")
        ):
            # 金鑰換過：更新 hash，這封信的退訂連結才有效
            store.set_unsubscribe_hash(user_id, token_hash)
        base = provider.public_base()
        message = render.brief_email(
            to=email,
            text=text,
            language=language,
            on_date=on_date,
            unsubscribe_url=f"{base}/api/email/unsubscribe?token={token}",
            manage_url=f"{base}/#connections",  # Email 設定 2026-09-28 起在 Connections
            # 同一人同一天一封（兩個帳號填同一個信箱也不會互相去重掉）
            idempotency_key=f"daily-brief/{hash_token(f'{user_id}:{email}')[:24]}/{on_date}",
        )
        ok = await sender.send(message)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — email 失敗不能拖垮其他管道
        logger.warning(
            "[email_brief] brief email failed user=%s: %s", user_id, type(exc).__name__
        )
        return False
    if not ok:
        logger.warning(
            "[email_brief] brief email not delivered user=%s to=%s",
            user_id,
            mask_email(email),
        )
    return ok
