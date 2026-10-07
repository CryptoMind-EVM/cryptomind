"""
警示分發器——AlertChannel 抽象 + 各平台 adapter

架構借鑑 Hermes / OpenClaw 的「抽象 adapter」模式（業界共識），但簡化為
單向推送（系統 → 用戶），不需 gateway daemon：

    AlertChannel (抽象介面)
      ├── InAppChannel       (v1，站內通知：create_notification + WebSocket)
      ├── TelegramChannel    (v1，既有 bot + telegram_bindings)
      ├── DiscordChannel     (v2，照 OpenClaw 流程，尚未實作)
      └── WhatsAppChannel    (遠期，Meta 審核+付費，尚未實作)

設計：
    - dispatch() 依用戶設定的 channels 呼叫對應 adapter。
    - 單一 adapter 失敗不影響其他（各自 try/except）。
    - 無私鑰/憑證在本模組——token 從 env 讀，chat id 從 telegram_bindings 反查。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

import httpx

from core.database.telegram import get_binding_by_user_id
from core.wallet_monitor.engine import AlertEvent

logger = logging.getLogger(__name__)


class AlertChannel(ABC):
    """警示管道抽象介面。加新平台 = 寫一個 adapter（學 Hermes/OpenClaw）。"""

    name: str = "base"

    @abstractmethod
    async def send(self, user_id: str, event: AlertEvent) -> bool:
        """送一則警示給指定用戶。回傳是否成功。"""


class InAppChannel(AlertChannel):
    """站內通知（create_notification + WebSocket 即時彈）。v1。"""

    name = "in_app"

    async def send(self, user_id: str, event: AlertEvent) -> bool:
        try:
            from core.database.notifications import create_notification

            title, body = format_alert_message(event)
            create_notification(
                user_id=user_id,
                notification_type="wallet_alert",
                title=title,
                body=body,
                data=event.to_dict(),
            )
            return True
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("[wallet_monitor] InAppChannel failed for %s: %s", user_id, exc)
            return False


class TelegramChannel(AlertChannel):
    """Telegram 推送（既有 bot token + telegram_bindings 反查 chat id）。v1。"""

    name = "telegram"

    async def send(self, user_id: str, event: AlertEvent) -> bool:
        import os

        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not token:
            logger.debug("[wallet_monitor] TG token not set, skip")
            return False
        binding = get_binding_by_user_id(user_id)
        if not binding:
            return False  # 用戶未綁 TG
        chat_id = binding.get("telegram_id")
        if not chat_id:
            return False

        title, body = format_alert_message(event)
        text = f"🔔 <b>{title}</b>\n\n{body}"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            return resp.status_code == 200
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except httpx.HTTPError as exc:
            logger.warning("[wallet_monitor] TG send failed: %s", exc)
            return False


def format_alert_message(event: AlertEvent) -> tuple[str, str]:
    """把 AlertEvent 轉成人類可讀的 (title, body)。"""
    emoji_map = {
        "incoming": "📥",
        "outgoing": "📤",
        "large_out": "🚨",
        "scam_contact": "⚠️",
    }
    emoji = emoji_map.get(event.event_type, "🔔")

    type_label = {
        "incoming": "轉入",
        "outgoing": "轉出",
        "large_out": "大額轉出",
        "scam_contact": "詐騙往來",
    }.get(event.event_type, event.event_type)

    amount = f"{event.amount_ton:,.4f}"  # 鏈中立格式（原生代幣量）
    cp = event.counterparty or "unknown"
    short_cp = cp[:12] + "..." if len(cp) > 12 else cp

    title = f"{emoji} 錢包{type_label}警示"
    body = (
        f"錢包: {event.wallet_address[:12]}...\n"
        f"類型: {type_label}\n"
        f"金額: {amount}\n"
        f"對端: {short_cp}"
    )
    if event.is_scam:
        body += "\n⚠️ 此交易涉及被標記的詐騙地址"
    return title, body


class AlertDispatcher:
    """依用戶設定分發警示到多個 channel。"""

    def __init__(self, channels: List[AlertChannel] | None = None) -> None:
        self._channels = channels or [InAppChannel(), TelegramChannel()]

    async def dispatch(self, user_id: str, events: List[AlertEvent], settings: Dict[str, Any]) -> int:
        """把事件分發到用戶啟用的 channels。回傳成功送達的 channel 次數。"""
        channel_cfg = (settings or {}).get("channels", {})
        sent = 0
        for ev in events:
            for ch in self._channels:
                enabled = channel_cfg.get(ch.name, ch.name == "in_app")
                if not enabled:
                    continue
                try:
                    if await ch.send(user_id, ev):
                        sent += 1
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[wallet_monitor] channel %s failed for %s: %s",
                        ch.name, user_id, exc,
                    )
        return sent


# 單例（供 cron/router 共用）。
alert_dispatcher = AlertDispatcher()


__all__ = ["AlertChannel", "InAppChannel", "TelegramChannel", "AlertDispatcher", "alert_dispatcher", "format_alert_message"]
