"""
Market WebSocket Endpoints
Real-time K-line and Ticker data streaming
"""

import asyncio
import json
from typing import Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.utils import logger

router = APIRouter()


async def _ws_authenticate(websocket: WebSocket) -> bool:
    """Verify JWT token from WebSocket query params or httpOnly cookie."""
    token = websocket.query_params.get("token") or websocket.cookies.get("access_token")
    if not token:
        return False
    try:
        from api.deps import verify_token

        verify_token(token)
        return True
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return False


# ============================================================================
# K-line WebSocket Manager
# ============================================================================


class KlineConnectionManager:
    """Manage K-line WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscriptions: dict = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        logger.debug(
            f"K-line WebSocket connected, total: {len(self.active_connections)}"
        )

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self.active_connections.discard(websocket)
            if websocket in self.subscriptions:
                del self.subscriptions[websocket]
        logger.debug(
            f"K-line WebSocket disconnected, total: {len(self.active_connections)}"
        )

    async def subscribe(self, websocket: WebSocket, symbol: str, interval: str):
        async with self._lock:
            self.subscriptions[websocket] = {"symbol": symbol, "interval": interval}
        logger.debug(f"Client subscribed: {symbol} {interval}")

    async def unsubscribe(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.subscriptions:
                del self.subscriptions[websocket]

    async def broadcast_kline(self, symbol: str, interval: str, kline: dict):
        """Broadcast K-line data to subscribed clients."""
        for ws, sub in list(self.subscriptions.items()):
            if sub["symbol"].upper() == symbol.upper() and sub["interval"] == interval:
                try:
                    await ws.send_json(
                        {
                            "type": "kline",
                            "symbol": symbol,
                            "interval": interval,
                            "data": kline,
                        }
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    logger.error(f"Broadcast failed: {e}")
                    await self.disconnect(ws)


kline_manager = KlineConnectionManager()
okx_ws_started = False
binance_ws_started = False


async def start_okx_websocket():
    """Start OKX WebSocket connection for K-lines."""
    global okx_ws_started
    if okx_ws_started:
        return

    try:
        from data.okx_websocket import okx_ws_manager

        okx_ws_started = True
        await okx_ws_manager.start()
    except ImportError as e:
        logger.error(f"Cannot import OKX WebSocket module: {e}")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to start OKX WebSocket: {e}")


async def start_binance_websocket():
    """Start Binance WebSocket connection for K-lines."""
    global binance_ws_started
    if binance_ws_started:
        return

    try:
        from data.binance_websocket import binance_ws_manager

        binance_ws_started = True
        await binance_ws_manager.start()
    except ImportError as e:
        logger.error(f"Cannot import Binance WebSocket module: {e}")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to start Binance WebSocket: {e}")


def _get_kline_ws_manager(exchange: str = "okx"):
    """根據交易所名稱回傳對應的 K 線 WebSocket manager"""
    if exchange.lower() == "binance":
        from data.binance_websocket import binance_ws_manager

        return binance_ws_manager
    from data.okx_websocket import okx_ws_manager

    return okx_ws_manager


@router.websocket("/ws/klines")
async def websocket_klines(websocket: WebSocket):
    """
    WebSocket endpoint for real-time K-line data.

    Client subscription format:
    {"action": "subscribe", "symbol": "BTC", "interval": "1m"}
    {"action": "unsubscribe"}

    Authentication: pass ?token=<jwt> in query string.
    """
    if not await _ws_authenticate(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # GAP-3: 背景 token 重驗。token 過期/revoke 時主動 close(4401)。
    token = websocket.query_params.get("token") or websocket.cookies.get("access_token")
    from api.routers.ws_auth import start_reauth_watcher

    reauth_task = await start_reauth_watcher(websocket, token)

    await kline_manager.connect(websocket)

    # 同時啟動 OKX 和 Binance WebSocket（依需求連接）
    asyncio.create_task(start_okx_websocket()).add_done_callback(
        lambda t: (
            t.exception() and logger.warning("OKX WS task failed: %s", t.exception())
        )
    )
    asyncio.create_task(start_binance_websocket()).add_done_callback(
        lambda t: (
            t.exception()
            and logger.warning("Binance WS task failed: %s", t.exception())
        )
    )

    current_subscription = None  # (symbol, interval, exchange)

    async def on_kline_update(symbol: str, interval: str, kline: dict):
        """Callback when K-line updates (works for both OKX and Binance)."""
        try:
            await websocket.send_json(
                {
                    "type": "kline",
                    "symbol": symbol,
                    "interval": interval,
                    "data": kline,
                }
            )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"Failed to send kline update: {e}")

    try:
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)

                action = message.get("action")

                if action == "subscribe":
                    symbol = message.get("symbol", "BTC").upper()
                    interval = message.get("interval", "1m")
                    exchange = message.get("exchange", "okx").lower()

                    # 取消舊訂閱（可能是不同交易所）
                    if current_subscription:
                        old_symbol, old_interval, old_exchange = current_subscription
                        old_manager = _get_kline_ws_manager(old_exchange)
                        await old_manager.unsubscribe(
                            old_symbol, old_interval, on_kline_update
                        )

                    # 訂閱新交易所
                    ws_manager = _get_kline_ws_manager(exchange)
                    await kline_manager.subscribe(websocket, symbol, interval)
                    subscribed = await ws_manager.subscribe(
                        symbol, interval, on_kline_update
                    )
                    if not subscribed:
                        # 訂閱被拒(達 WS 全局訂閱上限),通知 client 維持原狀
                        await kline_manager.unsubscribe(websocket)
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": "Subscription capacity reached. Try again later.",
                            }
                        )
                        continue
                    current_subscription = (symbol, interval, exchange)

                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "symbol": symbol,
                            "interval": interval,
                            "exchange": exchange,
                        }
                    )

                elif action == "unsubscribe":
                    if current_subscription:
                        old_symbol, old_interval, old_exchange = current_subscription
                        old_manager = _get_kline_ws_manager(old_exchange)
                        await old_manager.unsubscribe(
                            old_symbol, old_interval, on_kline_update
                        )
                        current_subscription = None

                    await kline_manager.unsubscribe(websocket)
                    await websocket.send_json({"type": "unsubscribed"})

                elif action == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})

    except WebSocketDisconnect:
        logger.info("K-line WebSocket client disconnected")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"K-line WebSocket error: {e}")
    finally:
        reauth_task.cancel()
        if current_subscription:
            try:
                old_symbol, old_interval, old_exchange = current_subscription
                old_manager = _get_kline_ws_manager(old_exchange)
                await old_manager.unsubscribe(old_symbol, old_interval)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(f"Failed to cleanup kline subscription: {e}")
        await kline_manager.disconnect(websocket)


# ============================================================================
# Ticker WebSocket Manager
# ============================================================================


class TickerConnectionManager:
    """Manage Ticker WebSocket connections."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.subscribed_symbols: dict = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        self.subscribed_symbols[websocket] = set()
        logger.debug(
            f"Ticker WebSocket connected, total: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        if websocket in self.subscribed_symbols:
            del self.subscribed_symbols[websocket]
        logger.debug(
            f"Ticker WebSocket disconnected, total: {len(self.active_connections)}"
        )

    def subscribe(self, websocket: WebSocket, symbols: list):
        if websocket not in self.subscribed_symbols:
            self.subscribed_symbols[websocket] = set()
        self.subscribed_symbols[websocket].update(symbols)

    def unsubscribe(self, websocket: WebSocket, symbols: list = None):
        if websocket in self.subscribed_symbols:
            if symbols:
                self.subscribed_symbols[websocket] -= set(symbols)
            else:
                self.subscribed_symbols[websocket].clear()


ticker_manager = TickerConnectionManager()
okx_ticker_ws_started = False
binance_ticker_ws_started = False


async def start_okx_ticker_websocket():
    """Start OKX Ticker WebSocket connection."""
    global okx_ticker_ws_started
    if okx_ticker_ws_started:
        logger.info("OKX Ticker WebSocket already running")
        return

    try:
        from data.okx_websocket import okx_ticker_ws_manager

        logger.info("Starting OKX Ticker WebSocket...")
        okx_ticker_ws_started = True
        await okx_ticker_ws_manager.start()
        logger.info("OKX Ticker WebSocket started")
    except ImportError as e:
        logger.error(f"Cannot import OKX Ticker WebSocket module: {e}")
        okx_ticker_ws_started = False
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to start OKX Ticker WebSocket: {e}")
        okx_ticker_ws_started = False


async def start_binance_ticker_websocket():
    """Start Binance Ticker WebSocket connection."""
    global binance_ticker_ws_started
    if binance_ticker_ws_started:
        logger.info("Binance Ticker WebSocket already running")
        return

    try:
        from data.binance_websocket import binance_ticker_ws_manager

        logger.info("Starting Binance Ticker WebSocket...")
        binance_ticker_ws_started = True
        await binance_ticker_ws_manager.start()
        logger.info("Binance Ticker WebSocket started")
    except ImportError as e:
        logger.error(f"Cannot import Binance Ticker WebSocket module: {e}")
        binance_ticker_ws_started = False
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Failed to start Binance Ticker WebSocket: {e}")
        binance_ticker_ws_started = False


def _get_ticker_ws_manager(exchange: str = "okx"):
    """根據交易所名稱回傳對應的 Ticker WebSocket manager"""
    if exchange.lower() == "binance":
        from data.binance_websocket import binance_ticker_ws_manager

        return binance_ticker_ws_manager
    from data.okx_websocket import okx_ticker_ws_manager

    return okx_ticker_ws_manager


@router.websocket("/ws/tickers")
async def websocket_tickers(websocket: WebSocket):
    """
    WebSocket endpoint for real-time Ticker data (Market Watch).

    Client subscription format:
    {"action": "subscribe", "symbols": ["BTC", "ETH", "SOL"]}
    {"action": "unsubscribe", "symbols": ["BTC"]}
    {"action": "unsubscribe_all"}

    Authentication: pass ?token=<jwt> in query string.
    """
    if not await _ws_authenticate(websocket):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # GAP-3: 背景 token 重驗。
    token = websocket.query_params.get("token") or websocket.cookies.get("access_token")
    from api.routers.ws_auth import start_reauth_watcher

    reauth_task = await start_reauth_watcher(websocket, token)

    await ticker_manager.connect(websocket)

    # 同時啟動 OKX 和 Binance Ticker WebSocket
    asyncio.create_task(start_okx_ticker_websocket()).add_done_callback(
        lambda t: (
            t.exception()
            and logger.warning("OKX Ticker WS task failed: %s", t.exception())
        )
    )
    asyncio.create_task(start_binance_ticker_websocket()).add_done_callback(
        lambda t: (
            t.exception()
            and logger.warning("Binance Ticker WS task failed: %s", t.exception())
        )
    )

    # key: "SYMBOL_exchange" -> callback，支援同一 symbol 在不同交易所
    current_callbacks = {}

    def _callback_key(symbol: str, exchange: str) -> str:
        return f"{symbol}_{exchange}"

    async def create_ticker_callback(symbol: str, exchange: str):
        """Create callback function for specific symbol + exchange."""

        async def on_ticker_update(sym: str, ticker: dict):
            try:
                await websocket.send_json(
                    {
                        "type": "ticker",
                        "symbol": symbol,
                        "exchange": exchange,
                        "data": ticker,
                    }
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.debug(f"Failed to send ticker update: {e}")

        return on_ticker_update

    try:
        while True:
            try:
                data = await websocket.receive_text()
                message = json.loads(data)

                action = message.get("action")
                exchange = message.get("exchange", "okx").lower()

                if action == "subscribe":
                    symbols = message.get("symbols", [])
                    if isinstance(symbols, str):
                        symbols = [symbols]

                    logger.debug(
                        f"Ticker subscription request: {symbols} on {exchange}"
                    )

                    for symbol in symbols:
                        symbol = symbol.upper()
                        cb_key = _callback_key(symbol, exchange)
                        if cb_key not in current_callbacks:
                            callback = await create_ticker_callback(symbol, exchange)
                            current_callbacks[cb_key] = (callback, exchange)
                            ws_mgr = _get_ticker_ws_manager(exchange)
                            await ws_mgr.subscribe(symbol, callback)
                            logger.debug(
                                f"Subscribed to Ticker: {symbol} on {exchange}"
                            )

                    ticker_manager.subscribe(websocket, symbols)
                    await websocket.send_json(
                        {"type": "subscribed", "symbols": symbols, "exchange": exchange}
                    )

                elif action == "unsubscribe":
                    symbols = message.get("symbols", [])
                    if isinstance(symbols, str):
                        symbols = [symbols]

                    for symbol in symbols:
                        symbol = symbol.upper()
                        cb_key = _callback_key(symbol, exchange)
                        if cb_key in current_callbacks:
                            callback, ex = current_callbacks[cb_key]
                            ws_mgr = _get_ticker_ws_manager(ex)
                            await ws_mgr.unsubscribe(symbol, callback)
                            del current_callbacks[cb_key]

                    ticker_manager.unsubscribe(websocket, symbols)
                    await websocket.send_json(
                        {
                            "type": "unsubscribed",
                            "symbols": symbols,
                            "exchange": exchange,
                        }
                    )

                elif action == "unsubscribe_all":
                    for cb_key, (callback, ex) in list(current_callbacks.items()):
                        ws_mgr = _get_ticker_ws_manager(ex)
                        # cb_key = "SYMBOL_exchange"，取 symbol 部分
                        symbol = cb_key.rsplit("_", 1)[0]
                        await ws_mgr.unsubscribe(symbol, callback)
                    current_callbacks.clear()
                    ticker_manager.unsubscribe(websocket)
                    await websocket.send_json({"type": "unsubscribed_all"})

                elif action == "ping":
                    await websocket.send_json({"type": "pong"})

            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})

    except WebSocketDisconnect:
        logger.info("Ticker WebSocket client disconnected")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Ticker WebSocket error: {e}")
    finally:
        reauth_task.cancel()
        try:
            for cb_key, (callback, ex) in current_callbacks.items():
                ws_mgr = _get_ticker_ws_manager(ex)
                symbol = cb_key.rsplit("_", 1)[0]
                await ws_mgr.unsubscribe(symbol, callback)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(f"Failed to cleanup ticker subscriptions: {e}")
        ticker_manager.disconnect(websocket)
