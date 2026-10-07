"""
Price Alert Background Checker

Polls current prices every 60 seconds and fires notifications when
alert conditions are met. Integrates with existing notification system.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

POLL_INTERVAL = 60  # seconds


def is_condition_met(
    condition: str,
    target: float,
    current_price: float,
    open_price: float,
) -> bool:
    """Evaluate whether an alert condition is triggered."""
    if condition == "above":
        return current_price >= target
    if condition == "below":
        return current_price <= target
    if open_price == 0:
        return False
    pct_change = (current_price - open_price) / open_price * 100
    if condition == "change_pct_up":
        return pct_change >= target
    if condition == "change_pct_down":
        return (-pct_change) >= target
    return False


def build_alert_body(alert: dict, current_price: float) -> str:
    """Build human-readable notification body for a triggered alert."""
    symbol = alert["symbol"]
    condition = alert["condition"]
    target = alert["target"]

    condition_labels = {
        "above": f"已突破目標價 {target:,.2f}",
        "below": f"已跌破目標價 {target:,.2f}",
        "change_pct_up": f"漲幅已達 {target:.1f}%",
        "change_pct_down": f"跌幅已達 {target:.1f}%",
    }
    label = condition_labels.get(condition, "條件已觸發")
    return f"{symbol} {label}，當前價格：{current_price:,.2f}"


_PROVIDER_CODES = {
    "hk_stock": "hk",
    "jp_stock": "jp",
    "kr_stock": "kr",
    "cn_stock": "cn",
    "in_stock": "in",
}


def _yf_quote(symbol: str) -> Optional[tuple]:
    """yfinance fast_info 的 (現價, 前一日收盤)；抓不到回 None。"""
    import yfinance as yf

    fi = yf.Ticker(symbol).fast_info
    last = fi.last_price
    if last is None or last != last or last <= 0:
        return None
    prev = fi.previous_close
    return (float(last), float(prev) if prev and prev == prev else float(last))


async def _fetch_price(symbol: str, market: str) -> Optional[tuple]:
    """
    Fetch (current_price, reference_price) for a symbol.
    reference_price: crypto＝24h 開盤；台股／美股＝前一日收盤（漲跌％的基準）。
    Returns None on failure.

    Note: For crypto we bypass the langchain @tool wrapper
    (core.agents.tools.get_crypto_price) because it returns
    {"price_info": "<formatted string>"} which has no numeric price field.
    Using OKXAPIConnector.get_ticker directly gives last + open24h.
    """
    from api.utils import run_sync

    try:
        if market == "crypto":
            def _get_crypto_price_robust(sym: str):
                """從 OKX 拿 last + open24h,失敗回 None。
                symbol 可能是 BTC / BTC-USDT / BTCUSDT 各種格式,統一轉成 OKX instId。
                """
                from utils.okx_api_connector import OKXAPIConnector

                # 正規化成 OKX instId 格式 (BTC-USDT)
                s = sym.upper().replace("/", "").replace("-", "")
                if s.endswith("USDT"):
                    s = s[:-4]
                elif s.endswith("USD") and s != "USDC":
                    s = s[:-3]
                inst_id = f"{s}-USDT"

                okx = OKXAPIConnector()
                resp = okx.get_ticker(inst_id)
                if not resp or resp.get("code") != "0":
                    return None
                data = (resp.get("data") or [{}])[0]
                last = data.get("last")
                open_24h = data.get("open24h")
                if last is None:
                    return None
                return (float(last), float(open_24h) if open_24h else float(last))

            return await run_sync(_get_crypto_price_robust, symbol)

        # 台股／美股的 *_stock_price 是 LangChain StructuredTool，要用 .invoke()——
        # 直接呼叫會 TypeError（'StructuredTool' object is not callable），被下面的 except
        # 吞掉後一律回 None：2026-09-27 正式機實測 2330／AAPL 都抓不到，台股／美股價格
        # 警報從工具化之後就沒觸發過、早報持倉也沒價格。
        # 漲跌基準用前一日收盤（prev_close）＝一般看盤的「今日漲跌」；沒有才退回開盤價。
        if market == "tw_stock":
            from core.tools.tw_stock_tools import tw_stock_price

            result = await run_sync(tw_stock_price.invoke, {"ticker": symbol})
            result = result if isinstance(result, dict) else {}
            price = result.get("current_price") or result.get("close") or result.get("price")
            ref = result.get("prev_close") or result.get("open") or price
            return (float(price), float(ref)) if price else None

        if market == "us_stock":
            from core.tools.us_stock_tools import us_stock_price

            result = await run_sync(us_stock_price.invoke, {"symbol": symbol})
            result = result if isinstance(result, dict) else {}
            price = result.get("price") or result.get("regularMarketPrice")
            ref = (
                result.get("prev_close")
                or result.get("regularMarketPreviousClose")
                or result.get("regularMarketOpen")
                or price
            )
            return (float(price), float(ref)) if price else None

        # 港／日／韓／陸／印股：各市場分頁用的同一個 provider（yfinance，代號帶後綴）
        if market in _PROVIDER_CODES:
            from core.providers.base_provider import get_provider

            provider = get_provider(_PROVIDER_CODES[market])
            result = await run_sync(provider.get_price, symbol)
            result = result if isinstance(result, dict) else {}
            price = result.get("price")
            ref = result.get("prev_close") or price
            return (float(price), float(ref)) if price else None

        # 商品期貨（GC=F）與外匯（EURUSD=X）：跟 commodity／forex 分頁一樣直接問 yfinance
        if market in ("commodity", "forex"):
            return await run_sync(_yf_quote, symbol)

    except Exception as e:
        logger.debug(f"Price fetch failed for {symbol} ({market}): {e}")
    return None


async def _check_single_alert(alert: dict) -> bool:
    """Check one alert and fire notification if triggered. Returns True if fired.

    Isolated as a coroutine so the outer loop can `asyncio.gather` with a
    semaphore — the old serial loop with N alerts × ~200ms per price fetch
    could exceed POLL_INTERVAL=60s once N > ~50, causing alerts to be
    checked too infrequently or pile up.
    """
    from api.routers.notifications import push_notification_to_user
    from api.utils import run_sync
    from core.database import mark_alert_triggered, rearm_alert

    prices = await _fetch_price(alert["symbol"], alert["market"])
    if prices is None:
        return False

    current_price, open_price = prices
    met = is_condition_met(
        alert["condition"], alert["target"], current_price, open_price
    )
    repeat = bool(alert.get("repeat"))
    # 重複警報：穿越那一次響，之後條件一直成立就不再響；條件解除才重新上膛。
    # 此前 triggered 寫了沒人讀，站上目標價期間每 60 秒響一次。
    if not met:
        if repeat and alert.get("triggered"):
            await run_sync(rearm_alert, alert["id"])
        return False
    if repeat and alert.get("triggered"):
        return False

    body = build_alert_body(alert, current_price)
    # 用 default-arg bind `alert` 避免 late-binding 陷阱(若上層用 gather 並行,
    # lambda 捕獲的 alert 會是最後一個迭代值)
    try:
        notification = await run_sync(
            _create_notification_sync, alert, body, current_price
        )
        if notification:
            await push_notification_to_user(alert["user_id"], notification)
        logger.info(
            f"Alert triggered: {alert['symbol']} ({alert['condition']} {alert['target']})"
        )
    except Exception as e:
        logger.error(f"Failed to send alert notification: {e}")
        return False

    await run_sync(mark_alert_triggered, alert["id"], repeat)
    return True


def _create_notification_sync(alert: dict, body: str, current_price: float):
    """Sync helper: create notification in DB. Bound parameters avoid lambda capture bugs."""
    from core.database.notifications import create_notification

    return create_notification(
        user_id=alert["user_id"],
        notification_type="price_alert",
        title=f"🔔 {alert['symbol']} 價格警報",
        body=body,
        data={
            "symbol": alert["symbol"],
            "market": alert["market"],
            "current_price": current_price,
            "alert_id": alert["id"],
        },
    )


# 並行上限:OKX API 有 rate limit,且 _db_executor 預設只有 10 thread
_ALERT_CONCURRENCY = 5


async def _check_all_alerts():
    """Run one check cycle across all active alerts (parallel with semaphore)."""
    from api.utils import run_sync
    from core.database import get_active_alerts

    alerts = await run_sync(get_active_alerts)

    if not alerts:
        return

    logger.debug(f"Checking {len(alerts)} active alerts")

    sem = asyncio.Semaphore(_ALERT_CONCURRENCY)

    async def _bounded(alert):
        async with sem:
            return await _check_single_alert(alert)

    fired = await asyncio.gather(*(_bounded(a) for a in alerts), return_exceptions=True)
    fired_count = sum(1 for r in fired if r is True)
    if fired_count:
        logger.info(f"Alert cycle: {fired_count}/{len(alerts)} triggered")


async def price_alert_check_task():
    """
    Background task: check all active alerts every POLL_INTERVAL seconds.
    Launched from api_server.py lifespan.
    """
    await asyncio.sleep(30)  # delay startup to let DB initialize
    logger.info("Price alert checker started")

    while True:
        try:
            await _check_all_alerts()
        except Exception as e:
            logger.error(f"Alert checker error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
