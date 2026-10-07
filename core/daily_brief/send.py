"""早報送出：Telegram（既有 bot token）＋站內通知＋Base App／Farcaster 宿主推播。失敗記 warning 不重試（明天再來）。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_MAX = 4000  # 官方上限 4096，留餘裕


async def send_telegram_text(chat_id: int, text: str) -> bool:
    """純文字送出（不用 HTML parse_mode——早報內容含使用者自填的分類與事件標題，
    不想為了跳脫符號出事）。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text[:TELEGRAM_MAX],
                    "disable_web_page_preview": True,
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "[daily_brief] TG send failed chat=%s status=%s",
                chat_id,
                resp.status_code,
            )
        return resp.status_code == 200
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except httpx.HTTPError as exc:
        logger.warning("[daily_brief] TG send error chat=%s: %s", chat_id, exc)
        return False


def send_inapp(
    user_id: str, title: str, body: str, *, on_date: Optional[str] = None
) -> bool:
    from core.database.notifications import create_notification

    try:
        row = create_notification(
            user_id=user_id,
            notification_type="daily_brief",
            title=title,
            body=body,
            data={"date": on_date} if on_date else None,
        )
        return row is not None
    except Exception as exc:  # noqa: BLE001 — 站內副本失敗不影響 Telegram 那份
        logger.warning(
            "[daily_brief] in-app notification failed user=%s: %s", user_id, exc
        )
        return False


def send_baseapp(
    user_id: str, title: str, body: str, *, on_date: Optional[str] = None
) -> bool:
    """Base App／Farcaster 宿主推播：沒 token 回 False（不算失敗）；有就送，任一 token 成功算送達。
    targetUrl 必須在本網域（宿主會擋）；notificationId 帶日期，同一天重跑宿主會去重。
    一人只推一則：Base App 送到了就不再走 Farcaster token（兩邊都有的人不會收到兩次）；
    Base App 沒開／沒對上／送失敗才退回 Farcaster。"""
    from api.public_base import public_base_url
    from core.miniapp_notifications import service

    delivered = False
    path = f"/?brief={on_date}" if on_date else "/"
    # (1) Base App：Dashboard API，以錢包地址為準（2026-04-09 起的正式路徑）
    try:
        r1 = service.notify_user_via_base_app(
            user_id, title=title, message=body, target_path=path
        )
        if r1 is not None:
            delivered = r1.any_success or delivered
            if r1.failed:
                logger.info(
                    "[daily_brief] base app push user=%s sent=%d failed=%s",
                    user_id,
                    len(r1.sent),
                    r1.failed,
                )
    except Exception as exc:  # noqa: BLE001 — 推播失敗不影響其他管道
        logger.warning("[daily_brief] base app push failed user=%s: %s", user_id, exc)
    if delivered:
        return True
    # (2) Farcaster 客戶端（Warpcast 等）：webhook 收到的 token
    try:
        base = public_base_url()
        r2 = service.notify_user(
            user_id,
            notification_id=f"daily-brief-{on_date or 'today'}",
            title=title,
            body=body,
            target_url=f"{base}{path}",
        )
        if r2 is not None:
            delivered = r2.any_success or delivered
            if r2.invalid or r2.rate_limited or r2.failed:
                logger.info(
                    "[daily_brief] farcaster push user=%s ok=%d invalid=%d rate_limited=%d failed=%d",
                    user_id,
                    len(r2.successful),
                    len(r2.invalid),
                    len(r2.rate_limited),
                    len(r2.failed),
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[daily_brief] farcaster push failed user=%s: %s", user_id, exc)
    return delivered
