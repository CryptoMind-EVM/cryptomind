"""
Telegram Mini App ``initData`` verification.

When a page is opened as a Telegram Mini App, Telegram injects a signed
``initData`` string (``window.Telegram.WebApp.initData``). Validating its
HMAC against the bot token proves the request really comes from that
Telegram user — no wallet signature needed. This is the native, reliable
way to authenticate inside a Mini App.

Algorithm (per Telegram Mini Apps docs):
    secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
    expected   = HMAC_SHA256(key=secret_key, msg=data_check_string)
where data_check_string is every field except ``hash``, sorted by key and
joined as ``key=value`` with newlines.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import parse_qsl

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# initData carries an auth_date; reject anything older than this to limit
# replay of a captured initData string.
TELEGRAM_INITDATA_TTL = 24 * 60 * 60  # 24 hours


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def verify_telegram_init_data(init_data: str) -> dict:
    """
    Validate a Telegram Mini App ``initData`` string and return the embedded
    Telegram user dict (``{"id": ..., "username": ..., "first_name": ...}``).

    Raises HTTPException on any validation failure.
    """
    token = _bot_token()
    if not token:
        # API service is missing the bot token → can't verify. Fail loudly
        # rather than silently letting anyone in.
        logger.error("TELEGRAM_BOT_TOKEN not set on API service; cannot verify initData")
        raise HTTPException(status_code=503, detail="Telegram login not configured")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    # parse_qsl gives us the percent-decoded values, which is what the
    # data_check_string must be built from.
    pairs = parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="initData missing hash")

    data_check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram initData signature")

    # Freshness: reject stale initData.
    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError:
        auth_date = 0
    if auth_date <= 0 or (time.time() - auth_date) > TELEGRAM_INITDATA_TTL:
        raise HTTPException(status_code=401, detail="initData expired")

    user_raw = data.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="initData missing user")
    try:
        user = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="initData user malformed") from exc
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="initData user has no id")

    return user
