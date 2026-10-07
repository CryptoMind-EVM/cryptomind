import asyncio
import logging
import threading
from typing import Any, Optional

from cachetools import TTLCache

logger = logging.getLogger("API")
logger.setLevel(logging.INFO)

MARKET_PULSE_CACHE: TTLCache = TTLCache(maxsize=500, ttl=300)
# TTL 600s（10 分鐘）：funding rate 每 8 小時結算，10 分鐘鮮度綽綽有餘。
# 先前 60s 強迫每分鐘重 hit OKX（不可靠的外部 API），是 funding rate
# 「Failed to load」toast 的主因之一。
FUNDING_RATE_CACHE: TTLCache = TTLCache(maxsize=100, ttl=600)

# Analysis Status Tracker
ANALYSIS_STATUS = {
    "is_running": False,
    "total": 0,
    "completed": 0,
    "current_batch": [],
    "start_time": None,
}

# Screener cache structure
cached_screener_result = {"timestamp": None, "data": None}

# Locks for concurrency control
screener_lock = asyncio.Lock()
funding_rate_lock = asyncio.Lock()
market_pulse_lock = threading.Lock()  # Sync context usage

# Global Instances
v4_manager: Optional[Any] = None  # ManagerAgent (V4) — 替代 CryptoAnalysisBot
