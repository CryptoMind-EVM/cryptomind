# ========================================
# Binance WebSocket 即時數據服務
# 鏡像 OKX WebSocket 架構，使用 Binance Stream API
# ========================================

import asyncio
import json
import logging
from typing import Callable, Dict, Optional, Set

try:
    import websockets
except ImportError:
    websockets = None

logger = logging.getLogger(__name__)

# Binance WebSocket 端點
BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

# 時間週期映射 (前端格式 -> Binance 格式)
# Binance 與前端格式基本一致，只需處理大小寫
INTERVAL_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}

WS_OPEN_TIMEOUT_SECONDS = 15
WS_RECONNECT_DELAY_SECONDS = 5
WS_RECONNECT_DELAY_MAX_SECONDS = 60

# Binance 每個連接最多訂閱 200 個 streams，我們遠低於此
BINANCE_MAX_STREAMS_PER_CONN = 200
# 自身保護上限:防止前端/客戶端錯誤導致無上限疊加訂閱吃光記憶體
# 每個訂閱 = 1 個 callback set + channel key 字串 + ws 內部 buffer
MAX_SUBSCRIPTIONS = 150


def _get_binance_symbol(symbol: str) -> str:
    """轉換幣種符號為 Binance 格式 (全大寫 + USDT)"""
    symbol = symbol.upper().replace("-", "")
    if symbol.endswith("USDT"):
        return symbol
    if symbol.endswith("USD") and symbol != "USDC":
        return symbol[:-3] + "USDT"
    return symbol + "USDT"


class BinanceWebSocketManager:
    """管理 Binance K 線 WebSocket 連接和訂閱"""

    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.subscriptions: Dict[str, Set[Callable]] = {}  # channel -> callbacks
        self.running = False
        self.reconnect_delay = WS_RECONNECT_DELAY_SECONDS
        self._connect_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None
        self._msg_id = 1

    def _get_channel_key(self, symbol: str, interval: str) -> str:
        return f"{symbol}_{interval}"

    def _reset_reconnect_delay(self) -> None:
        self.reconnect_delay = WS_RECONNECT_DELAY_SECONDS

    def _increase_reconnect_delay(self) -> int:
        delay = self.reconnect_delay
        self.reconnect_delay = min(delay * 2, WS_RECONNECT_DELAY_MAX_SECONDS)
        return delay

    async def connect(self):
        if websockets is None:
            logger.error("websockets 模組未安裝，請執行: pip install websockets")
            return

        self.running = True

        while self.running:
            try:
                logger.info(f"正在連接 Binance WebSocket: {BINANCE_WS_BASE}")

                async with websockets.connect(
                    BINANCE_WS_BASE,
                    open_timeout=WS_OPEN_TIMEOUT_SECONDS,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.ws = ws
                    self._reset_reconnect_delay()
                    logger.info("Binance WebSocket 連接成功")

                    await self._resubscribe_all()

                    # Binance 不需要 ping，服務端會自動發 ping
                    async for message in ws:
                        await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Binance WebSocket 連接關閉: {e}")
            except TimeoutError as e:
                logger.warning(f"Binance WebSocket opening handshake timed out: {e}")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"Binance WebSocket 錯誤: {e}")
            finally:
                self.ws = None

            if self.running:
                delay = self._increase_reconnect_delay()
                logger.info(f"{delay} 秒後重新連接 Binance...")
                await asyncio.sleep(delay)

    async def _resubscribe_all(self):
        if not self.ws or not self.subscriptions:
            return

        for channel_key in self.subscriptions.keys():
            parts = channel_key.split("_")
            if len(parts) == 2:
                symbol, interval = parts
                await self._send_subscribe(symbol, interval)

    async def _send_subscribe(self, symbol: str, interval: str):
        if not self.ws:
            return

        binance_symbol = _get_binance_symbol(symbol)
        binance_interval = INTERVAL_MAP.get(interval, "1m")
        stream = f"{binance_symbol.lower()}@kline_{binance_interval}"

        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [stream],
            "id": self._msg_id,
        }
        self._msg_id += 1

        await self.ws.send(json.dumps(subscribe_msg))
        logger.debug(f"Binance 訂閱: {stream}")

    async def _send_unsubscribe(self, symbol: str, interval: str):
        if not self.ws:
            return

        binance_symbol = _get_binance_symbol(symbol)
        binance_interval = INTERVAL_MAP.get(interval, "1m")
        stream = f"{binance_symbol.lower()}@kline_{binance_interval}"

        unsubscribe_msg = {
            "method": "UNSUBSCRIBE",
            "params": [stream],
            "id": self._msg_id,
        }
        self._msg_id += 1

        try:
            await self.ws.send(json.dumps(unsubscribe_msg))
            logger.debug(f"Binance 取消訂閱: {stream}")
        except websockets.exceptions.ConnectionClosed:
            self.ws = None
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"Binance 取消訂閱失敗: {e}")

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)

            # 訂閱確認 / 錯誤
            if "result" in data or "id" in data:
                if "error" in data:
                    logger.error(f"Binance 訂閱錯誤: {data}")
                return

            # K 線數據推送
            if data.get("e") == "kline":
                kline_data = data.get("k", {})
                symbol_raw = data.get("s", "")  # BTCUSDT
                interval = kline_data.get("i", "")  # 1m

                # 反查前端的 interval 格式
                front_interval = interval
                for fi, bi in INTERVAL_MAP.items():
                    if bi == interval:
                        front_interval = fi
                        break

                kline = self._parse_candle(kline_data)

                # 嘗試多種 channel_key 格式來匹配訂閱
                symbol_variants = [
                    symbol_raw,  # BTCUSDT
                    symbol_raw.replace("USDT", ""),  # BTC
                    symbol_raw.lower(),  # btcusdt
                ]

                for sym in symbol_variants:
                    channel_key = self._get_channel_key(sym, front_interval)
                    if channel_key in self.subscriptions:
                        logger.debug(f"收到 Binance K 線數據: {channel_key}")
                        for callback in self.subscriptions[channel_key]:
                            try:
                                await callback(sym, front_interval, kline)
                            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                                raise
                            except Exception as e:
                                logger.error(f"Binance 回調錯誤: {e}")
                        break

        except json.JSONDecodeError:
            logger.warning(f"Binance 無法解析消息: {message[:100]}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Binance 處理消息錯誤: {e}")

    def _parse_candle(self, k: dict) -> dict:
        """解析 Binance K 線數據為標準格式"""
        return {
            "time": int(k.get("t", 0)) // 1000,  # 毫秒轉秒
            "open": float(k.get("o", 0)),
            "high": float(k.get("h", 0)),
            "low": float(k.get("l", 0)),
            "close": float(k.get("c", 0)),
            "volume": float(k.get("v", 0)),
            "confirmed": k.get("x", False),  # x=true 表示 K 線已收盤
        }

    async def subscribe(self, symbol: str, interval: str, callback: Callable) -> bool:
        """訂閱 K 線數據。回傳 True 成功;若達訂閱上限回傳 False。"""
        channel_key = self._get_channel_key(symbol, interval)

        if channel_key not in self.subscriptions:
            # 訂閱總數 cap:超過時拒絕新訂閱並記 log,避免記憶體無上限成長
            if len(self.subscriptions) >= MAX_SUBSCRIPTIONS:
                logger.warning(
                    f"Binance WS subscription cap reached ({MAX_SUBSCRIPTIONS}), "
                    f"ignoring new subscribe for {channel_key}"
                )
                return False
            self.subscriptions[channel_key] = set()
            if self.ws:
                await self._send_subscribe(symbol, interval)

        self.subscriptions[channel_key].add(callback)
        logger.debug(f"Binance 添加訂閱回調: {channel_key}")
        return True

    async def unsubscribe(self, symbol: str, interval: str, callback: Callable = None):
        channel_key = self._get_channel_key(symbol, interval)

        if channel_key in self.subscriptions:
            if callback:
                self.subscriptions[channel_key].discard(callback)
            else:
                self.subscriptions[channel_key].clear()

            if not self.subscriptions[channel_key]:
                del self.subscriptions[channel_key]
                if self.ws:
                    await self._send_unsubscribe(symbol, interval)

    async def start(self):
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = asyncio.create_task(self.connect())

    async def stop(self):
        self.running = False
        if self.ws:
            await self.ws.close()
        if self._connect_task:
            self._connect_task.cancel()


# 全局實例
binance_ws_manager = BinanceWebSocketManager()


class BinanceTickerWebSocketManager:
    """管理 Binance Ticker WebSocket 連接和訂閱"""

    def __init__(self):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.subscriptions: Dict[str, Set[Callable]] = {}  # symbol -> callbacks
        self.running = False
        self.reconnect_delay = WS_RECONNECT_DELAY_SECONDS
        self._connect_task: Optional[asyncio.Task] = None
        self._msg_id = 1000  # 避免與 kline manager 的 id 衝突

    def _reset_reconnect_delay(self) -> None:
        self.reconnect_delay = WS_RECONNECT_DELAY_SECONDS

    def _increase_reconnect_delay(self) -> int:
        delay = self.reconnect_delay
        self.reconnect_delay = min(delay * 2, WS_RECONNECT_DELAY_MAX_SECONDS)
        return delay

    async def connect(self):
        if websockets is None:
            logger.error("websockets 模組未安裝，請執行: pip install websockets")
            return

        self.running = True

        while self.running:
            try:
                logger.info(f"正在連接 Binance Ticker WebSocket: {BINANCE_WS_BASE}")

                async with websockets.connect(
                    BINANCE_WS_BASE,
                    open_timeout=WS_OPEN_TIMEOUT_SECONDS,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self.ws = ws
                    self._reset_reconnect_delay()
                    logger.info("Binance Ticker WebSocket 連接成功")

                    await self._resubscribe_all()

                    async for message in ws:
                        await self._handle_message(message)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"Binance Ticker WebSocket 連接關閉: {e}")
            except TimeoutError as e:
                logger.warning(
                    f"Binance Ticker WebSocket opening handshake timed out: {e}"
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error(f"Binance Ticker WebSocket 錯誤: {e}")
            finally:
                self.ws = None

            if self.running:
                delay = self._increase_reconnect_delay()
                logger.info(f"{delay} 秒後重新連接 Binance Ticker...")
                await asyncio.sleep(delay)

    async def _resubscribe_all(self):
        if not self.ws or not self.subscriptions:
            return

        for symbol in self.subscriptions.keys():
            await self._send_subscribe(symbol)

    async def _send_subscribe(self, symbol: str):
        if not self.ws:
            return

        binance_symbol = _get_binance_symbol(symbol)
        stream = f"{binance_symbol.lower()}@ticker"

        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [stream],
            "id": self._msg_id,
        }
        self._msg_id += 1

        await self.ws.send(json.dumps(subscribe_msg))
        logger.debug(f"Binance 訂閱 Ticker: {stream}")

    async def _send_unsubscribe(self, symbol: str):
        if not self.ws:
            return

        binance_symbol = _get_binance_symbol(symbol)
        stream = f"{binance_symbol.lower()}@ticker"

        unsubscribe_msg = {
            "method": "UNSUBSCRIBE",
            "params": [stream],
            "id": self._msg_id,
        }
        self._msg_id += 1

        try:
            await self.ws.send(json.dumps(unsubscribe_msg))
            logger.debug(f"Binance 取消訂閱 Ticker: {stream}")
        except websockets.exceptions.ConnectionClosed:
            self.ws = None
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning(f"Binance 取消訂閱 Ticker 失敗: {e}")

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)

            # 訂閱確認 / 錯誤
            if "result" in data or ("id" in data and "e" not in data):
                if "error" in data:
                    logger.error(f"Binance Ticker 訂閱錯誤: {data}")
                return

            # 24hr Ticker 推送
            if data.get("e") == "24hrTicker":
                symbol_raw = data.get("s", "")  # BTCUSDT
                parsed = self._parse_ticker(data)

                # 嘗試多種 symbol 格式來匹配訂閱
                symbol_variants = [
                    symbol_raw,  # BTCUSDT
                    symbol_raw.replace("USDT", ""),  # BTC
                    symbol_raw.lower(),  # btcusdt
                ]

                for symbol in symbol_variants:
                    if symbol in self.subscriptions:
                        for callback in self.subscriptions[symbol]:
                            try:
                                await callback(symbol, parsed)
                            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                                raise
                            except Exception as e:
                                logger.error(f"Binance Ticker 回調錯誤: {e}")
                        break

        except json.JSONDecodeError:
            logger.warning(f"Binance Ticker 無法解析消息: {message[:100]}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f"Binance Ticker 處理消息錯誤: {e}")

    def _parse_ticker(self, data: dict) -> dict:
        """解析 Binance Ticker 數據為標準格式"""
        return {
            "symbol": data.get("s", ""),
            "last": float(data.get("c", 0)),  # 最新成交價
            "open24h": float(data.get("o", 0)),  # 24h 開盤價
            "high24h": float(data.get("h", 0)),  # 24h 最高
            "low24h": float(data.get("l", 0)),  # 24h 最低
            "vol24h": float(data.get("v", 0)),  # 24h 成交量 (base asset)
            "volCcy24h": float(data.get("q", 0)),  # 24h 成交量 (quote asset)
            "change24h": float(data.get("P", 0)),  # 24h 漲跌幅 %
            "ts": int(data.get("E", 0)),  # 事件時間
        }

    async def subscribe(self, symbol: str, callback: Callable) -> bool:
        """訂閱 Ticker。回傳 True 成功;若達訂閱上限回傳 False。"""
        if symbol not in self.subscriptions:
            # 訂閱總數 cap:超過時拒絕新訂閱並記 log
            if len(self.subscriptions) >= MAX_SUBSCRIPTIONS:
                logger.warning(
                    f"Binance Ticker WS subscription cap reached ({MAX_SUBSCRIPTIONS}), "
                    f"ignoring new subscribe for {symbol}"
                )
                return False
            self.subscriptions[symbol] = set()
            if self.ws:
                await self._send_subscribe(symbol)

        self.subscriptions[symbol].add(callback)
        logger.info(f"Binance 添加 Ticker 訂閱回調: {symbol}")
        return True

    async def unsubscribe(self, symbol: str, callback: Callable = None):
        if symbol in self.subscriptions:
            if callback:
                self.subscriptions[symbol].discard(callback)
            else:
                self.subscriptions[symbol].clear()

            if not self.subscriptions[symbol]:
                del self.subscriptions[symbol]
                if self.ws:
                    await self._send_unsubscribe(symbol)

    async def subscribe_many(self, symbols: list, callback: Callable) -> int:
        """批量訂閱。回傳成功訂閱數量。"""
        succeeded = 0
        for symbol in symbols:
            if await self.subscribe(symbol, callback):
                succeeded += 1
        return succeeded

    async def unsubscribe_all(self):
        symbols = list(self.subscriptions.keys())
        for symbol in symbols:
            try:
                await self.unsubscribe(symbol)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.warning(f"Binance 批量取消訂閱失敗 {symbol}: {e}")

    async def start(self):
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = asyncio.create_task(self.connect())

    async def stop(self):
        self.running = False
        if self.ws:
            await self.ws.close()
        if self._connect_task:
            self._connect_task.cancel()


# Ticker 全局實例
binance_ticker_ws_manager = BinanceTickerWebSocketManager()
