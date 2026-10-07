"""TON 鏈介面卡（路線 C）——包裝既有 engine.py 的 TonAPI 邏輯。

既有 engine.py 的 fetch_events / match_rules 仍是 TON 專屬的「原始」實作；
本 adapter 是薄包裝，讓引擎能依 chain 統一分派。實際 TonAPI 呼仍在 engine.py。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base import ChainBalance, WalletChainAdapter

logger = logging.getLogger(__name__)


class TonAdapter(WalletChainAdapter):
    """TON 鏈介面卡（TonAPI）。"""

    @property
    def chain(self) -> str:
        return "ton"

    @property
    def native_symbol(self) -> str:
        return "TON"

    def is_valid_address(self, address: str) -> bool:
        from core.onchain.addresses import is_ton_address

        return is_ton_address(address)

    async def fetch_events(
        self, address: str, api_key: Optional[str] = None, *, user_id: str
    ) -> List[Dict[str, Any]]:
        # 委派給 engine.py（TonAPI events endpoint），跟 cron 的 TON 路徑一樣從這個用戶
        # 上次處理到的那筆往後抓。api_key 不適用（TON 用 TonAPI 免費端點），忽略。
        from core.wallet_monitor.engine import fetch_new_events

        return await fetch_new_events(address, user_id=user_id)

    def translate_events(
        self,
        address: str,
        raw_events: List[Dict[str, Any]],
        settings: Dict[str, Any],
        *,
        user_id: str,
    ) -> List[Any]:
        # 委派給 engine.py 的既有 match_rules（TON 專屬事件翻譯）
        from core.wallet_monitor.engine import match_rules

        return match_rules(address, raw_events, settings, user_id=user_id)

    async def fetch_balance(self, address: str, api_key: Optional[str] = None) -> Optional[ChainBalance]:
        from core.tools.crypto_modules.ton_balance import get_ton_balance

        try:
            result = await get_ton_balance.ainvoke({"address": address})
            if isinstance(result, dict):
                amt = float(result.get("balance_ton", 0))
                return ChainBalance(amount=amt, symbol="TON", chain="ton")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[TonAdapter] balance failed %s: %s", address[:12], exc)
        return None
