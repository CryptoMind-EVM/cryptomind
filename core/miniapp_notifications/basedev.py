"""Base App 通知（Base Dashboard REST API）——2026-04-09 起 Base App 不再走 Farcaster 規格。

使用者在 Base App 裡「收藏（pin）」我們並開通知後，Dashboard API 會回他的錢包地址；我們用
帳號綁定的 EVM 地址對上，就能推。端點共用一把 API key（Dashboard → Settings → API Key，
env ``BASE_DASHBOARD_API_KEY``，header ``x-api-key``），共用 20 req/min/IP 的速率上限，所以
名單快取 10 分鐘、兩次呼叫至少隔 MIN_INTERVAL、送出時一次最多 1000 個地址。
title ≤30、message ≤200、target_path 要以 / 開頭。

**預設關**：要 ``BASE_APP_NOTIFICATIONS_ENABLED=true`` 且有 key 才會打 API（2026-09-24）——
VM 上先放 key 不會在下一個整點就對所有人推；首次全體推播由 DANNY 打開旗標。

規格：docs.base.org apps/technical-guides/base-notifications（2026-05-04 版）；2026-08-27 docs 改版
把頁面拿掉（API 仍在線上），原文：github.com/base/docs/blob/3e3bf4ce9e/docs/apps/technical-guides/
base-notifications.mdx。設計與上線步驟：docs/plans/2026-09-24-base-app-notifications.md。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://dashboard.base.org/api/v1/notifications"
TITLE_MAX = 30
MESSAGE_MAX = 200
TARGET_PATH_MAX = 500
BATCH = 1000
_AUDIENCE_TTL = 600
_audience_cache: Dict[str, tuple[float, Set[str]]] = {}
# 20 req/min/IP（名單與送出共用）→ 每次至少隔 3.1 秒；早報 cron 是逐人送，超過 20 人就會吃 429
MIN_INTERVAL = 3.1
_last_call = 0.0
_disabled_logged = False


def api_key() -> str:
    return os.getenv("BASE_DASHBOARD_API_KEY", "").strip()


def app_url() -> str:
    """Dashboard 上登記的 app URL（含結尾斜線與否要跟 Dashboard 一致；預設用對外 base）。"""
    from api.public_base import public_base_url

    return (os.getenv("BASE_APP_URL", "").strip() or public_base_url() + "/").strip()


def flag_on() -> bool:
    """總開關，預設關（FLAG_REGISTRY 登記的就是這個）。"""
    from core.feature_flags import env_flag

    return env_flag("BASE_APP_NOTIFICATIONS_ENABLED")


def enabled() -> bool:
    """旗標＋key 都要有才算開。關的時候原因只記一次（cron 逐人呼叫，不要洗版）。"""
    global _disabled_logged
    flag = flag_on()
    key = bool(api_key())
    if flag and key:
        return True
    if not _disabled_logged:
        _disabled_logged = True
        logger.info(
            "[basedev] Base App notifications off (BASE_APP_NOTIFICATIONS_ENABLED=%s, "
            "BASE_DASHBOARD_API_KEY %s); Base App push is a no-op",
            "on" if flag else "off",
            "set" if key else "missing",
        )
    return False


def _pace() -> None:
    """兩次 Dashboard 呼叫至少隔 MIN_INTERVAL 秒（同一個 process 內）。"""
    global _last_call
    wait = MIN_INTERVAL - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _checksum(addr: str) -> Optional[str]:
    """EIP-55；不是合法 EVM 地址回 None。Base 的 status 端點也是先正規化成這個形式再查。"""
    from eth_utils import to_checksum_address

    try:
        return to_checksum_address(str(addr).strip())
    except (ValueError, TypeError):
        return None


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def opted_in_addresses(
    *, timeout: float = 10.0, force: bool = False
) -> Optional[Set[str]]:
    """收藏＋開通知的錢包地址（小寫）。None＝查不到（沒 key／API 壞）；快取 10 分鐘。"""
    if not enabled():
        return None
    url = app_url()
    hit = _audience_cache.get(url)
    now = time.time()
    if hit and not force and now - hit[0] < _AUDIENCE_TTL:
        return hit[1]
    out: Set[str] = set()
    cursor: Optional[str] = None
    try:
        for _ in range(20):  # 最多 20 頁 × 500
            params = {"app_url": url, "notification_enabled": "true", "limit": 500}
            if cursor:
                params["cursor"] = cursor
            _pace()
            resp = httpx.get(
                f"{API_BASE}/app/users",
                params=params,
                headers={"x-api-key": api_key()},
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[basedev] users status=%s body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            data = resp.json()
            for u in data.get("users") or []:
                addr = str(u.get("address") or "").lower()
                if addr.startswith("0x") and u.get("notificationsEnabled", True):
                    out.add(addr)
            cursor = data.get("nextCursor")
            if not cursor:
                break
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[basedev] users fetch failed: %s", exc)
        return None
    _audience_cache[url] = (now, out)
    return out


def clear_cache() -> None:
    _audience_cache.clear()


@dataclass
class BaseSendResult:
    sent: List[str] = field(default_factory=list)
    failed: Dict[str, str] = field(default_factory=dict)

    @property
    def any_success(self) -> bool:
        return bool(self.sent)


def send(
    addresses: Iterable[str],
    *,
    title: str,
    message: str,
    target_path: Optional[str] = None,
    timeout: float = 10.0,
) -> BaseSendResult:
    """對錢包地址推播。地址去重、轉 EIP-55、每批 ≤1000；同內容 24 小時內 Dashboard 會去重。
    HTTP／網路錯誤只記在 ``failed``，不往外丟（早報其他管道照送）。"""
    result = BaseSendResult()
    if not enabled():
        return result
    canon = {c.lower(): c for c in (_checksum(a) for a in addresses) if c}
    addrs = [canon[k] for k in sorted(canon)]
    if not addrs:
        return result
    body = {
        "app_url": app_url(),
        "title": clip(title, TITLE_MAX),
        "message": clip(message, MESSAGE_MAX),
    }
    if target_path:
        tp = target_path if target_path.startswith("/") else "/" + target_path
        body["target_path"] = tp[:TARGET_PATH_MAX]
    for i in range(0, len(addrs), BATCH):
        batch = addrs[i : i + BATCH]
        try:
            _pace()
            resp = httpx.post(
                f"{API_BASE}/send",
                json={**body, "wallet_addresses": batch},
                headers={"x-api-key": api_key()},
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("[basedev] send failed: %s", exc)
            for a in batch:
                result.failed[a.lower()] = "http error"
            continue
        if resp.status_code != 200:
            # 401 key 錯／403 app_url 對不上或專案沒開通知／429 超速／503 對方暫時掛
            logger.warning(
                "[basedev] send status=%s body=%s", resp.status_code, resp.text[:200]
            )
            for a in batch:
                result.failed[a.lower()] = f"status {resp.status_code}"
            continue
        try:
            data = resp.json() if resp.content else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        for r in data.get("results") or []:
            a = str(r.get("walletAddress") or "").lower()
            if r.get("sent"):
                result.sent.append(a)
            else:
                result.failed[a] = str(r.get("failureReason") or "not sent")
    return result
