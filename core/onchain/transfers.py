"""統一的轉帳紀錄（EVM／TON 來源都轉成這個），再由 sync 對應成帳本條目。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 價格固定 1 USD 的資產（TON 上的 USDT 代號是 USD₮，先正規化）
STABLECOINS = {"USDC", "USDT", "DAI", "USDE", "FDUSD", "PYUSD", "USDS"}
SYMBOL_ALIASES = {
    "USD₮": "USDT",
    "JUSDT": "USDT",
    "WETH": "ETH",
    "WBTC": "BTC",
    "CBBTC": "BTC",
}


def normalize_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    return SYMBOL_ALIASES.get(s, s)


@dataclass(frozen=True)
class Transfer:
    chain: str  # base | ethereum | ton …
    tx_hash: str
    timestamp: int  # unix seconds
    direction: str  # "in" | "out"
    symbol: str  # 原始代號（顯示用）
    amount: float  # 人類單位
    counterparty: str = ""
    contract: Optional[str] = None  # ERC-20／jetton 合約；原生幣 None
    fee_native: float = 0.0  # 轉出時的 gas（原生幣）
    native_symbol: str = ""
    is_scam: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def price_symbol(self) -> str:
        """拿去查價的代號（WETH→ETH、USD₮→USDT）。"""
        return normalize_symbol(self.symbol)
