"""錢包監測（Wallet Monitor）— trust_score 的消費面。

子模組：
    engine.py   事件抓取（TonAPI events）+ 規則比對 → AlertEvent
    channels.py AlertChannel 抽象 + InApp/Telegram adapter + dispatcher

對應 design doc：docs/plans/2026-08-08-wallet-monitor-dashboard-design.md
"""

from core.wallet_monitor.channels import (
    AlertChannel,
    AlertDispatcher,
    InAppChannel,
    TelegramChannel,
    alert_dispatcher,
)
from core.wallet_monitor.engine import AlertEvent, fetch_events, match_rules

__all__ = [
    "AlertEvent",
    "AlertChannel",
    "AlertDispatcher",
    "InAppChannel",
    "TelegramChannel",
    "alert_dispatcher",
    "fetch_events",
    "match_rules",
]
