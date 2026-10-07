"""TON jetton 安全訊號規則（純規則，safety-kernel）。

從 core/tools/crypto_modules/ton_safety.py 抽出的審查核心規則（行為不變）：
  - whitelist  → 官方驗證（如 USDt、STON），綠燈
  - 其他       → 未驗證，警覺（黃燈以上）
  - 代幣不存在 → 硬擋（可能是詐騙地址或貼錯）

本模組不發任何網路請求——TonAPI 的呼叫留在主平台
（core/tools/crypto_modules/ton_safety.py），這裡只負責：
  1. TokenSafety 的資料結構與衍生訊號（is_verified / is_low_liquidity）
  2. build_token_safety：從原始欄位 + 額外訊號建出 TokenSafety（純函式）

純函式、無 I/O——審查員可逐行核對門檻與訊號規則。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# 持有者數量門檻（少於此數代表流動性/信任不足）。
# 依據：spike 中 USDt=336 萬、STON=7 萬，皆遠高於此；詐騙幣常 < 100。
LOW_HOLDER_THRESHOLD = 1_000


@dataclass
class TokenSafety:
    """TON jetton 的安全評估結果（給 workflow 決策用）。"""

    address: str
    # TonAPI 原始欄位
    verification: str  # whitelist / blacklist / none / unknown
    symbol: str
    name: str
    holders_count: int
    has_admin: bool
    exists: bool
    # 衍生的風險訊號（給 risk_level 計算用）
    signals: list[str] = field(default_factory=list)
    # 來源標記
    source: str = "tonapi"

    @property
    def is_verified(self) -> bool:
        """是否通過官方驗證（whitelist）。"""
        return self.verification == "whitelist"

    @property
    def is_low_liquidity(self) -> bool:
        """持有者過少，流動性風險。"""
        return self.exists and self.holders_count < LOW_HOLDER_THRESHOLD


def build_token_safety(
    *,
    address: str,
    verification: str,
    symbol: str,
    name: str,
    holders_count: int,
    has_admin: bool,
    exists: bool,
    extra_signals: Optional[list[str]] = None,
) -> TokenSafety:
    """從原始資料建出 TokenSafety（純函式），並累積標準風險訊號。

    Args:
        extra_signals: 呼叫端補充的訊號（例如「代幣不存在」「TonAPI 不可用」）。
            網路層的錯誤資訊由主平台注入，本模組不碰網路。

    標準訊號（有資料就加）：
      - 未驗證 → "Token is not officially verified (not whitelisted)"
      - 低流動性 → "Only N holders — very low liquidity"
      - 有 admin → "Contract has admin rights (...)"
    """
    safety = TokenSafety(
        address=address,
        verification=verification,
        symbol=symbol,
        name=name,
        holders_count=int(holders_count or 0),
        has_admin=bool(has_admin),
        exists=bool(exists),
    )

    if not safety.is_verified:
        safety.signals.append("Token is not officially verified (not whitelisted)")
    if safety.is_low_liquidity:
        safety.signals.append(f"Only {safety.holders_count} holders — very low liquidity")
    if safety.has_admin:
        safety.signals.append("Contract has admin rights (may be able to change token rules)")
    if extra_signals:
        safety.signals.extend(extra_signals)

    return safety


def safety_to_dict(s: TokenSafety) -> dict[str, Any]:
    """序列化 TokenSafety 給 workflow state / 報價卡 UI。"""
    return {
        "address": s.address,
        "symbol": s.symbol,
        "name": s.name,
        "verification": s.verification,
        "is_verified": s.is_verified,
        "holders_count": s.holders_count,
        "is_low_liquidity": s.is_low_liquidity,
        "has_admin": s.has_admin,
        "exists": s.exists,
        "signals": s.signals,
        "source": s.source,
    }
