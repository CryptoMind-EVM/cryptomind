"""錢包監測鏈介面卡基底（路線 C）。

docs/plans/2026-08-11-wallet-monitor-multichain-design.md

每條鏈實作一個 adapter，提供：
- fetch_events(address) → list[AlertEvent]（鏈上活動，翻譯成統一格式）
- fetch_balance(address) → (amount, symbol)（原生代幣餘額）
- is_valid_address(address) → bool

引擎（engine.py）依 chain 分派到對應 adapter。評分/通知/HITL 共用，
加一條鏈只要加一個 adapter。
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChainBalance:
    """鏈上餘額查詢結果。"""
    amount: float
    symbol: str  # "TON" / "ETH" / "BNB" ...
    chain: str   # "ton" / "eth" / "bsc" ...


class WalletChainAdapter(abc.ABC):
    """錢包監測的鏈介面卡抽象基底。"""

    @property
    @abc.abstractmethod
    def chain(self) -> str:
        """鏈識別碼（ton / eth / bsc / polygon / ...）。"""

    @property
    @abc.abstractmethod
    def native_symbol(self) -> str:
        """原生代幣符號（TON / ETH / BNB / MATIC / ...）。"""

    @abc.abstractmethod
    async def fetch_events(
        self, address: str, api_key: Optional[str] = None, *, user_id: str
    ) -> List[Dict[str, Any]]:
        """抓取錢包最近活動（raw event dicts）。失敗回空 list。

        api_key：模式 B——使用者的 BYOK key（cron 傳入）；None 時由 adapter 自行 fallback。
        user_id：監測的用戶——去重游標按 (用戶, 鏈, 地址) 分開存。
        """

    @abc.abstractmethod
    def translate_events(
        self,
        address: str,
        raw_events: List[Dict[str, Any]],
        settings: Dict[str, Any],
        *,
        user_id: str,
    ) -> List[Any]:
        """把 raw events 翻譯成 AlertEvent，並推進這個用戶的去重游標。"""

    @abc.abstractmethod
    async def fetch_balance(self, address: str, api_key: Optional[str] = None) -> Optional[ChainBalance]:
        """查原生代幣餘額。失敗回 None。"""

    @abc.abstractmethod
    def is_valid_address(self, address: str) -> bool:
        """驗證地址格式是否符合此鏈。"""
