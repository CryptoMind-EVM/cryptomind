"""
通用工具
非加密貨幣特定的通用功能工具
"""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from .schemas import CurrentTimeInput


@tool
def introduction_tool(_query: str = "") -> str:
    """
    Introduces the platform and developer information.

    Use cases:
    - User asks "who are the developers?" or "who built this platform?"
    - User asks "who developed this system?" or "who is the author?"
    - User wants to know the project background or dev team info
    """
    return (
        "Platform developer details:\n"
        "CryptoMind is an independently developed open-source project and is not affiliated with any organization.\n"
        "Tech stack: FastAPI + LangGraph multi-agent + EVM wallet integration (USDC on Base).\n"
        "GitHub: https://github.com/CryptoMind-EVM/cryptomind"
    )


@tool(args_schema=CurrentTimeInput)
def get_current_time_tool(timezone: str = "Asia/Taipei") -> str:
    """
    Get the current date and time.

    Queries the current time in different timezones, defaulting to Taipei (UTC+8).

    Use cases:
    - User asks "what time is it now?"
    - User asks "what is today's date?"
    - User asks what time it is in a specific timezone
    """
    try:
        # 嘗試使用指定的時區
        try:
            tz = ZoneInfo(timezone)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # 如果時區無效，使用台北時間
            tz = ZoneInfo("Asia/Taipei")
            timezone = "Asia/Taipei"

        now = datetime.now(tz)

        # 格式化時間
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        weekday_map = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        weekday = weekday_map[now.weekday()]

        # 判斷是上午還是下午
        period = "AM" if now.hour < 12 else "PM"
        hour_12 = now.hour if now.hour <= 12 else now.hour - 12
        if hour_12 == 0:
            hour_12 = 12
        time_12_str = f"{period} {hour_12}:{now.strftime('%M')}"

        return f"""## 🕐 Current Time

| Item | Value |
|------|-------|
| **Date** | {date_str} ({weekday}) |
| **Time** | {time_str} ({time_12_str}) |
| **Timezone** | {timezone} |
| **UTC Time** | {now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%d %H:%M:%S")} |
"""

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        return f"Error fetching the time: {str(e)}"
