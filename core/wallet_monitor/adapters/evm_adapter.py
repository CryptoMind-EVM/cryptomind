"""EVM 鏈介面卡（路線 C）——服務級 Etherscan V2 多鏈。

docs/plans/2026-08-11-wallet-monitor-multichain-design.md

與 etherscan.py 的 BYOK 工具不同——錢包監測是背景 cron 輪詢，沒有使用者
的 key 可用。本 adapter 用服務級 ETHERSCAN_SERVICE_API_KEY。

Phase 1 支援 Ethereum mainnet + BSC + Polygon（最高流量三條）。
其他鏈只要 chain_id 對就可用（_SUPPORTED_CHAINS 可擴充）。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from core.shared_cache import get_json, set_json

from .base import ChainBalance, WalletChainAdapter

logger = logging.getLogger(__name__)

_ETHERSCAN_ENDPOINT = "https://api.etherscan.io/v2/api"
_WEI_PER_ETH = 10**18
# 每頁筆數／往舊翻頁上限（同 TON engine：兩次輪詢之間超過一頁也不漏，翻頁有上限）。
_TX_PAGE_SIZE = 20
_MAX_TX_PAGES = 5
# 上次處理到的 tx hash（去重游標）。同一個 EVM 地址在不同鏈是不同的交易串，key 要帶 chain_id；
# 也要帶用戶——同一個錢包被兩個人監測，先輪到的人推進游標，另一個人就收不到那些交易。
_LAST_TX_KEY = "wallet_monitor:user_last_tx:{chain_id}:{user_id}:{address}"
# 舊 key（只按鏈＋地址）——只在上線那一輪讀，見 _last_seen_tx（同 engine 的 TON 游標）。
_LEGACY_LAST_TX_KEY = "wallet_monitor:last_tx:{chain_id}:{address}"
_LAST_TX_TTL = 7 * 24 * 3600  # 同 engine._LAST_EVENT_TTL：撐過停機，恢復後翻頁補抓

# chain_id → (chain_label, native_symbol, decimals)
# ⚠️ 實測免費 Etherscan key 只支援部分鏈（Ethereum/Polygon/Arbitrum）。
# BSC/Optimism/Base/Avalanche 需付費 plan。僅列免費可用的鏈，
# 避免使用者設了 BYOK key 卻查不到資料。付費 plan 可自行擴充此表。
_SUPPORTED_CHAINS = {
    1: ("ethereum", "ETH", 18),
    137: ("polygon", "MATIC", 18),
    42161: ("arbitrum", "ETH", 18),
}

# chain_name → chain_id（解析用）
_CHAIN_NAME_TO_ID = {
    "eth": 1, "ethereum": 1,
    "polygon": 137, "matic": 137,
    "arbitrum": 42161, "arb": 42161,
}


def _service_etherscan_key() -> Optional[str]:
    """取得服務級 Etherscan key（fallback，模式 B 主要用使用者 BYOK key）。"""
    return os.getenv("ETHERSCAN_SERVICE_API_KEY")


def resolve_evm_chain_id(name: str) -> Optional[int]:
    """鏈名 → chain_id；未知回 None。"""
    return _CHAIN_NAME_TO_ID.get((name or "").strip().lower())


class EvmAdapter(WalletChainAdapter):
    """EVM 鏈介面卡（Etherscan V2，模式 B：使用者 BYOK key 優先，服務 key fallback）。"""

    def __init__(self, chain_id: int):
        if chain_id not in _SUPPORTED_CHAINS:
            raise ValueError(f"Unsupported EVM chain_id {chain_id}")
        self._chain_id = chain_id
        self._label, self._symbol, self._decimals = _SUPPORTED_CHAINS[chain_id]

    @property
    def chain(self) -> str:
        return self._label

    @property
    def native_symbol(self) -> str:
        return self._symbol

    def is_valid_address(self, address: str) -> bool:
        addr = (address or "").strip()
        return addr.startswith("0x") and len(addr) == 42

    def _etherscan_get(self, params: dict, api_key: Optional[str] = None) -> Optional[dict]:
        """Etherscan V2 呼叫。模式 B：優先用使用者 BYOK key，fallback 服務 key。

        無任何 key → None（graceful，該錢包跳過）。
        """
        key = api_key or _service_etherscan_key()
        if not key:
            logger.debug("[EvmAdapter] no Etherscan key (BYOK or service) — skipping")
            return None
        query = {"chainid": self._chain_id, **params, "apikey": key}
        try:
            resp = httpx.get(_ETHERSCAN_ENDPOINT, params=query, timeout=15.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[EvmAdapter] etherscan failed: %s", exc)
            return None

    def _cursor_key(self, user_id: str, address: str) -> str:
        return _LAST_TX_KEY.format(
            chain_id=self._chain_id, user_id=user_id, address=address.lower()
        )

    def _last_seen_tx(self, user_id: str, address: str) -> Optional[str]:
        """這個用戶上次處理到的 tx hash；per-user 游標還沒有（剛上線）就讀一次舊的
        per-address 游標，免得每個既有監測都把最新一頁當新交易重發。"""
        last = get_json(self._cursor_key(user_id, address))
        if last is None:
            last = get_json(
                _LEGACY_LAST_TX_KEY.format(
                    chain_id=self._chain_id, address=address.lower()
                )
            )
        return last

    async def fetch_events(
        self, address: str, api_key: Optional[str] = None, *, user_id: str
    ) -> List[Dict[str, Any]]:
        """Etherscan txlist → 統一 event dict（incoming/outgoing），只回這個用戶上次處理到之後的。

        模式 B：api_key 是使用者的 BYOK key（cron 傳入）；None 時 fallback 服務 key。
        有游標（上次處理到的 tx hash）就往舊翻頁直到碰到它，最多 _MAX_TX_PAGES 頁；
        沒游標（第一次）只看最新一頁。第一頁失敗回空；後面的頁失敗跟碰到上限一樣，
        已抓到的較新交易照常回傳、記 warning。
        """
        if not self.is_valid_address(address):
            return []
        last_hash = str(self._last_seen_tx(user_id, address) or "").lower()
        events: List[Dict[str, Any]] = []
        stopped = f"page cap ({_MAX_TX_PAGES} pages)"
        for page in range(1, _MAX_TX_PAGES + 1):
            data = self._etherscan_get({
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": 0,
                "endblock": 99999999,
                "page": page,
                "offset": _TX_PAGE_SIZE,
                "sort": "desc",
            }, api_key=api_key)
            if data and data.get("status") != "1" and page > 1 and data.get("result") == []:
                return events  # 翻過頭："No transactions found"（status 0、result 空）
            if not data or data.get("status") != "1":
                if not events:
                    return []
                stopped = f"page {page} failed"
                break
            rows = (data.get("result") or [])[:_TX_PAGE_SIZE]
            for tx in rows:
                tx_hash = tx.get("hash", "")
                if last_hash and tx_hash.lower() == last_hash:
                    return events
                is_outgoing = tx.get("from", "").lower() == address.lower()
                events.append({
                    "event_id": tx_hash,
                    "event_type": "outgoing" if is_outgoing else "incoming",
                    "amount_raw": int(tx.get("value", 0)),
                    "counterparty": tx.get("to" if is_outgoing else "from", ""),
                    "is_scam": False,  # EVM 無免費 scam 標記；Phase 2 接 GoPlus
                    "timestamp": int(tx.get("timeStamp", 0)),
                })
            if not last_hash or len(rows) < _TX_PAGE_SIZE:
                return events
        logger.warning(
            "[EvmAdapter] %s chain %s: stopped at %s before last seen tx; "
            "processing %d newer txs, older txs skipped",
            address[:12], self._chain_id, stopped, len(events),
        )
        return events

    def translate_events(
        self,
        address: str,
        raw_events: List[Dict[str, Any]],
        settings: Dict[str, Any],
        *,
        user_id: str,
    ) -> List[Any]:
        """把統一 event dict 翻譯成 AlertEvent（USD 門檻比對），並把這個用戶的游標推到最新一筆。"""
        from core.wallet_monitor.engine import AlertEvent

        last = self._last_seen_tx(user_id, address)
        if last is None:
            # 第一次輪詢（剛加入監測、也沒有舊游標）：只記基準、不警示（同 engine.match_rules；
            # 抓不到交易就先不記，等抓得到的那輪再當基準）
            baseline = next(
                (e["event_id"] for e in raw_events if e.get("event_id")), None
            )
            if baseline:
                set_json(self._cursor_key(user_id, address), baseline, _LAST_TX_TTL)
                logger.info(
                    "[EvmAdapter] %s chain %s: first poll for user %s, baseline set (no alerts)",
                    address[:12],
                    self._chain_id,
                    user_id,
                )
            return []

        # 游標每輪都寫（沒新交易就原值重寫刷新 TTL，否則安靜的錢包游標過期後會整頁重發）
        newest = raw_events[0].get("event_id") if raw_events else last
        if newest:
            set_json(self._cursor_key(user_id, address), newest, _LAST_TX_TTL)
        result = []
        incoming_cfg = settings.get("alerts", {}).get("incoming", {})
        outgoing_cfg = settings.get("alerts", {}).get("outgoing", {})
        large_out_cfg = settings.get("alerts", {}).get("large_out", {})

        # 簡化：用 raw amount（wei）的 log10 當近似量級。
        # Phase 2 用真實幣價換算 USD 門檻。
        for ev in raw_events:
            amount_raw = ev.get("amount_raw", 0)
            amount = amount_raw / (10 ** self._decimals) if self._decimals else amount_raw
            etype = ev.get("event_type", "")
            if etype == "incoming" and incoming_cfg.get("enabled"):
                result.append(AlertEvent(
                    wallet_address=address,
                    event_id=ev.get("event_id", ""),
                    event_type="incoming",
                    amount_ton=amount,  # 欄位名歷史遺留；值是原生幣量
                    counterparty=ev.get("counterparty", ""),
                    is_scam=ev.get("is_scam", False),
                    timestamp=ev.get("timestamp", 0),
                ))
            elif etype == "outgoing":
                if large_out_cfg.get("enabled") and amount >= float(large_out_cfg.get("min_amount_ton", 999999)):
                    result.append(AlertEvent(
                        wallet_address=address,
                        event_id=ev.get("event_id", ""),
                        event_type="large_out",
                        amount_ton=amount,
                        counterparty=ev.get("counterparty", ""),
                        is_scam=False,
                        timestamp=ev.get("timestamp", 0),
                    ))
                elif outgoing_cfg.get("enabled"):
                    result.append(AlertEvent(
                        wallet_address=address,
                        event_id=ev.get("event_id", ""),
                        event_type="outgoing",
                        amount_ton=amount,
                        counterparty=ev.get("counterparty", ""),
                        is_scam=False,
                        timestamp=ev.get("timestamp", 0),
                    ))
        return result

    async def fetch_balance(self, address: str, api_key: Optional[str] = None) -> Optional[ChainBalance]:
        if not self.is_valid_address(address):
            return None
        data = self._etherscan_get({
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
        }, api_key=api_key)
        if not data or data.get("status") != "1":
            return None
        amount = int(data["result"]) / (10 ** self._decimals)
        return ChainBalance(amount=amount, symbol=self._symbol, chain=self._label)
