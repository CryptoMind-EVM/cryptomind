"""webhook 事件處理與「送給某使用者」的入口。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import store
from .jfs import JfsError, VerifiedEvent, verify_jfs
from .key_registry import app_key_active
from .send import SendResult, send_notification

logger = logging.getLogger(__name__)

EVENTS_WITH_TOKEN = {"miniapp_added", "frame_added", "notifications_enabled"}
EVENTS_DISABLE = {"miniapp_removed", "frame_removed", "notifications_disabled"}


def _verify_app_key(fid: int, key: str) -> bool:
    ok = app_key_active(fid, key)
    if ok is None:
        # 鏈上查不到就拒收，宿主會重送（fail closed：假 token 只會讓我們對錯的網址發通知，
        # 但假 removed 事件會把真使用者的通知關掉，寧可暫時漏收）
        raise JfsError("key registry unavailable")
    return ok


def parse_event(body: Any) -> VerifiedEvent:
    return verify_jfs(body, _verify_app_key)


def handle_event(event: VerifiedEvent) -> Dict[str, Any]:
    name = str(event.payload.get("event") or "")
    if name in EVENTS_WITH_TOKEN:
        details = event.payload.get("notificationDetails") or {}
        url, token = details.get("url"), details.get("token")
        if not (isinstance(url, str) and isinstance(token, str) and url and token):
            return {"ok": False, "event": name, "reason": "missing notificationDetails"}
        if not url.startswith("https://"):
            return {
                "ok": False,
                "event": name,
                "reason": "notification url must be https",
            }
        store.upsert_token(event.fid, url, token)
        return {"ok": True, "event": name, "fid": event.fid}
    if name in EVENTS_DISABLE:
        n = store.disable_by_fid(event.fid, name)
        return {"ok": True, "event": name, "fid": event.fid, "disabled": n}
    return {"ok": False, "event": name, "reason": "unknown event"}


def notify_user(
    user_id: str,
    *,
    notification_id: str,
    title: str,
    body: str,
    target_url: str,
) -> Optional[SendResult]:
    """Farcaster 客戶端（Warpcast 等）：用 webhook 收到的 token 送。沒有 token 回 None。"""
    targets = store.tokens_for_user(user_id)
    if not targets:
        return None
    return send_notification(
        targets,
        notification_id=notification_id,
        title=title,
        body=body,
        target_url=target_url,
    )


def notify_user_via_base_app(
    user_id: str, *, title: str, message: str, target_path: Optional[str] = None
):
    """Base App（2026-04-09 起不走 Farcaster 規格）：帳號綁定的 EVM 地址 ∩ Dashboard 回的
    「收藏＋開通知」名單，命中就用 Dashboard API 推。回 None＝沒開（旗標／key）／沒名單／沒對上。"""
    from . import basedev

    if not basedev.enabled():
        return None
    audience = basedev.opted_in_addresses()
    if not audience:
        return None
    from core.onchain.store import list_wallets

    mine = {
        str(w.get("address") or "").lower()
        for w in list_wallets(user_id)
        if w.get("chain") == "evm"
    }
    hit = sorted(mine & audience)
    if not hit:
        return None
    return basedev.send(hit, title=title, message=message, target_path=target_path)


def base_app_opted_in(user_id: str) -> bool:
    """設定頁用：這個帳號有沒有任一綁定 EVM 地址在 Base App 收藏＋開通知。"""
    from . import basedev

    if not basedev.enabled():
        return False
    audience = basedev.opted_in_addresses()
    if not audience:
        return False
    from core.onchain.store import list_wallets

    return any(
        str(w.get("address") or "").lower() in audience
        for w in list_wallets(user_id)
        if w.get("chain") == "evm"
    )
