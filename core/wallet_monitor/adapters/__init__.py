"""錢包監測鏈介面卡註冊表（路線 C）。

get_adapter(chain) 回傳對應的 WalletChainAdapter。
加一條鏈只要在這裡註冊一個 adapter 實例。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from .base import ChainBalance, WalletChainAdapter
from .evm_adapter import EvmAdapter, resolve_evm_chain_id
from .ton_adapter import TonAdapter

logger = logging.getLogger(__name__)

# 單例快取（adapter 無狀態，可共用）
_ton_adapter: Optional[TonAdapter] = None
_evm_adapters: Dict[int, EvmAdapter] = {}


def get_adapter(chain: str) -> Optional[WalletChainAdapter]:
    """依 chain 識別碼回傳介面卡。未知鏈回 None。

    Args:
        chain: "ton" / "eth" / "bsc" / "polygon" / "arbitrum" / "optimism" / "base"
    """
    global _ton_adapter, _evm_adapters

    chain_lower = (chain or "").strip().lower()

    if chain_lower in ("ton", ""):
        if _ton_adapter is None:
            _ton_adapter = TonAdapter()
        return _ton_adapter

    # EVM 鏈
    chain_id = resolve_evm_chain_id(chain_lower)
    if chain_id is not None:
        if chain_id not in _evm_adapters:
            try:
                _evm_adapters[chain_id] = EvmAdapter(chain_id)
            except ValueError:
                return None
        return _evm_adapters[chain_id]

    logger.warning("[adapters] unknown chain: %s", chain)
    return None


def detect_chain(address: str) -> str:
    """依地址格式偵測鏈（TON 各種寫法 vs EVM 0x）。"""
    from core.onchain.addresses import is_ton_address

    addr = (address or "").strip()
    if is_ton_address(addr):
        return "ton"
    if addr.startswith("0x") and len(addr) == 42:
        return "eth"  # 預設 Ethereum；caller 可覆寫
    return "ton"  # 安全預設


__all__ = [
    "ChainBalance",
    "WalletChainAdapter",
    "TonAdapter",
    "EvmAdapter",
    "get_adapter",
    "detect_chain",
    "resolve_evm_chain_id",
]
