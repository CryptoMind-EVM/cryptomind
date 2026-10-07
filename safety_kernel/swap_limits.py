"""Swap 單筆 USD 限額規則（純規則，safety-kernel）。

從 core/tools/crypto_modules/swap_limits.py 抽出的審查核心規則（行為不變）：
  - USD 計價（非 TON）：TON 幣價波動大，USD 上限才穩定。
  - fail-closed：查不到幣價 → 拒絕，不冒險放行未知金額。
  - 與同意卡的區別：同意卡 = 「你要不要做這筆」（控制權）；
    限額 = 「就算你同意，系統也不讓你一次賭太大」（防 bug 後備保險）。

幣價查詢（CoinGecko）與快取留在主平台（swap_limits.py）——本模組只做
「拿到價格後」的純決策，審查員可逐行核對金額上限與降級規則。

規則參數：
  max_usd 由主平台傳入（env 可調，灰度期 50 USD）——參數化讓「規則」與
  「營運設定」分離：規則（本檔）可公開審計，設定（env）可被營運調整。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Union

# 1 TON = 1e9 nanoTON。
NANO_PER_TON = 1_000_000_000


@dataclass
class SwapLimitCheck:
    """限額檢查結果。"""

    allowed: bool
    max_usd: float
    input_usd: float  # 這筆 swap 的 USD 等值（查不到幣價時為 0）
    max_input_nano: int  # 換算後的 nanoTON 上限
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "max_usd": self.max_usd,
            "input_usd": round(self.input_usd, 2),
            "max_input_nano": str(self.max_input_nano),
            "reason": self.reason,
        }


def check_limit_with_price(
    input_units_nano: Union[str, int],
    ton_usd_price: Optional[float],
    max_usd: float,
) -> SwapLimitCheck:
    """檢查一筆 swap 是否在 USD 限額內（純決策，價格由呼叫端提供）。

    Args:
        input_units_nano: 付出資產數量（nanoTON）。若 input 不是 native TON
            （例如 jetton→TON），caller 應先換算成 TON 等值再傳入。
        ton_usd_price: TON/USD 即時價。None 或 <= 0 → fail-closed 拒絕。
        max_usd: 單筆上限（USD）。

    Returns:
        SwapLimitCheck。查不到幣價時 allowed=False（fail-closed）。
    """
    try:
        nano = int(input_units_nano)
    except (TypeError, ValueError):
        return SwapLimitCheck(
            allowed=False,
            max_usd=max_usd,
            input_usd=0.0,
            max_input_nano=0,
            reason="invalid input_units format",
        )

    if nano <= 0:
        return SwapLimitCheck(
            allowed=False,
            max_usd=max_usd,
            input_usd=0.0,
            max_input_nano=0,
            reason="input_units must be positive",
        )

    price = ton_usd_price
    if price is None or price <= 0:
        # fail-closed：查不到幣價就不允許，不冒險放行未知金額。
        return SwapLimitCheck(
            allowed=False,
            max_usd=max_usd,
            input_usd=0.0,
            max_input_nano=0,
            reason="Unable to fetch the current TON price; swap paused for safety (fail-closed)",
        )

    input_ton = nano / NANO_PER_TON
    input_usd = input_ton * price
    max_input_ton = max_usd / price
    max_input_nano = int(max_input_ton * NANO_PER_TON)

    if input_usd > max_usd:
        return SwapLimitCheck(
            allowed=False,
            max_usd=max_usd,
            input_usd=input_usd,
            max_input_nano=max_input_nano,
            reason=(
                f"This swap is worth about {input_usd:.2f} USD, exceeding the single-transaction cap "
                f"of {max_usd:.0f} USD. Please reduce the amount (max about {max_input_ton:.2f} TON)."
            ),
        )

    return SwapLimitCheck(
        allowed=True,
        max_usd=max_usd,
        input_usd=input_usd,
        max_input_nano=max_input_nano,
    )
