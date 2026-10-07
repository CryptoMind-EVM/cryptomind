"""Price impact 色階 + 綜合風險分級（純規則，safety-kernel）。

從 core/agents/manager/swap_pipeline.py 抽出的審查核心規則（行為不變）：
  - < 1%    green（綠燈）
  - 1-3%    yellow（黃燈，提示）
  - 3-5%    orange（橙燈，觸發同意卡）
  - 5-15%   red（紅燈，強烈警告 + 同意）
  - >= 15%  block（黑燈，硬擋——Uniswap 有 expert 後門可繞過，我們不給）

安全訊號（safety_rules.TokenSafety）會把等級往上推：
  未驗證 / 低流動性 → 至少 yellow；代幣不存在 → block。

純函式、無 I/O——審查員可逐行核對閾值與分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from safety_kernel.safety_rules import TokenSafety

# 門檻（percent）。10000 pips = 1%，這裡用百分比。
TIER_GREEN_PCT = 1.0  # < 1%  綠燈
TIER_YELLOW_PCT = 3.0  # 1-3%  黃燈
TIER_RED_PCT = 5.0  # 3-5%  橙燈（觸發同意）；5-15% 紅燈
TIER_BLOCK_PCT = 15.0  # >= 15% 黑燈硬擋


@dataclass
class RiskAssessment:
    """綜合風險評估結果。"""

    level: str  # green / yellow / orange / red / block
    needs_consent: bool  # 是否需要使用者顯式同意才能繼續
    blocked: bool  # 是否硬擋（不可繼續）
    reasons: list[str] = field(default_factory=list)


def assess_risk(price_impact_percent: float, safety: Optional[TokenSafety]) -> RiskAssessment:
    """綜合 price impact 色階 + 代幣安全訊號，產出風險分級。

    純函式，獨立可測。規則：
      - 代幣不存在 → block（硬擋）
      - price impact >= 15% → block（Uniswap 的 expert 後門，我們不給）
      - price impact 5-15% → red（強烈警告 + 同意）
      - price impact 3-5% → orange（觸發同意卡）
      - price impact 1-3% → yellow（提示）
      - price impact < 1% → green
      安全訊號會把等級往上推（未驗證代幣、低流動性 → 至少 yellow）。
    """
    reasons: list[str] = []
    blocked = False
    needs_consent = False

    # 代幣安全訊號
    if safety is not None:
        if not safety.exists:
            blocked = True
            reasons.append("目標代幣在 TON 鏈上不存在（可能是詐騙或地址貼錯）")
        if safety.is_verified:
            reasons.append(f"代幣已通過官方驗證（{safety.symbol}）")
        else:
            reasons.append("代幣未通過官方驗證（非 whitelist）")
        if safety.is_low_liquidity:
            reasons.append(f"持有者僅 {safety.holders_count} 人，流動性極低")
        if safety.has_admin:
            reasons.append("合約有 admin 權限（可能可改代幣規則）")

    # Price impact 色階
    pi = price_impact_percent
    if pi >= TIER_BLOCK_PCT:
        blocked = True
        reasons.append(f"價格影響 {pi}% 過高（>={TIER_BLOCK_PCT}%），交易會嚴重虧損")
    elif pi >= TIER_RED_PCT:  # 5-15%
        level = "red"
        needs_consent = True
        reasons.append(f"價格影響 {pi}% 偏高，強烈建議重新考慮")
    elif pi >= TIER_YELLOW_PCT:  # 3-5%
        level = "orange"
        needs_consent = True
        reasons.append(f"價格影響 {pi}%，需要你確認才能繼續")
    elif pi >= TIER_GREEN_PCT:  # 1-3%
        level = "yellow"
        reasons.append(f"價格影響 {pi}%（略高）")
    else:  # < 1%
        level = "green"

    # blocked 優先
    if blocked:
        return RiskAssessment(level="block", needs_consent=False, blocked=True, reasons=reasons)

    # 安全訊號升級：未驗證/低流動性至少 yellow
    if safety is not None and not safety.is_verified and level == "green":
        level = "yellow"

    return RiskAssessment(
        level=level,
        needs_consent=needs_consent,
        blocked=blocked,
        reasons=reasons,
    )
