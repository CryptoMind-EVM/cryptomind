"""詐騙判定信心分計算（純函式，零依賴）。

為 Trustworthy AI Hackathon 2026 場景 B 輕量版 HITL 設計：把 GoPlus
_extract_risk_signals 的結構化信號轉成可解釋的信心分 + 加權明細，供前端
渲染「為什麼 70%」的證據鏈卡片。

設計原則：
- 加權表寫死在檔頭（可審，評審問「70% 怎麼來」一行行看）
- breakdown 帶中文 reason，前端可直接展示
- 含任一致命高風險信號 → confidence 下限 65（避免加權微調遮蔽致命風險）
- confidence = clamp(base + sum(weights), 0, 100)
"""

from __future__ import annotations

from typing import Any, Dict, List

# ─────────────────────────────────────────────────────────────────────────────
# 加權表（可審）。對齊 _extract_risk_signals 的信號 key（goplus.py）。
# 評審問「為什麼 honeypot 是 +35」：蜜罐是 GoPlus 對 bytecode 的確定性判定，
# 命中即接近肯定詐騙；hidden_owner +12 是 rug pull 前兆但非充分條件。
# ─────────────────────────────────────────────────────────────────────────────
_HIGH_RISK_WEIGHTS: Dict[str, int] = {
    "is_honeypot": 35,
    "cannot_buy": 35,
    "mintable_and_owner_change_balance": 35,
    "selfdestruct": 35,
    "honeypot_with_same_creator": 30,
    # TON（路線 B）：代幣不存在 = 高風險（詐騙/打錯）
    "token_not_found": 40,
}

_WARNING_WEIGHTS: Dict[str, int] = {
    "not_open_source": 10,
    "hidden_owner": 12,
    "slippage_modifiable": 10,
    "is_proxy": 8,
    "transfer_pausable": 10,
    "mintable": 8,
    "external_call": 5,
    # TON（路線 B）
    "not_whitelisted": 12,
    "is_low_liquidity": 10,
    "has_admin": 10,
}

_SAFETY_WEIGHTS: Dict[str, int] = {
    "trust_list": -30,
    "is_in_cex": -25,
    "is_in_dex": -5,
    # TON（路線 B）：官方白名單 = 信任 offset
    "ton_whitelisted": -25,
}

# 中文 reason 對照表（前端可直接展示「為什麼這個分數」）
_REASON_ZH: Dict[str, str] = {
    "is_honeypot": "蜜罐（只能買不能賣，典型騙局）",
    "cannot_buy": "合約禁止買入",
    "mintable_and_owner_change_balance": "owner 可無限鑄造 + 改你的餘額（rug pull 前兆）",
    "selfdestruct": "合約可自毀，資金可能歸零",
    "honeypot_with_same_creator": "同一創建者曾部署蜜罐合約",
    "not_open_source": "合約未開源，無法審計",
    "hidden_owner": "隱藏 owner（表面放棄權限但實際保留）",
    "slippage_modifiable": "可修改滑點/稅率",
    "is_proxy": "代理合約，邏輯可被替換",
    "transfer_pausable": "可暫停轉帳，owner 可凍結你的代幣",
    "mintable": "可鑄造新幣，可能稀釋持有者價值",
    "external_call": "合約有外部呼叫",
    "trust_list": "在 GoPlus 信任清單",
    "is_in_cex": "已上架 CEX",
    "is_in_dex": "在 DEX 有流動性",
    # TON（路線 B）
    "token_not_found": "代幣在 TON 鏈上不存在（可能是詐騙地址或打錯地址）",
    "not_whitelisted": "TON jetton 未經官方白名單驗證",
    "is_low_liquidity": "持有人數極少，流動性很低",
    "has_admin": "合約有 admin 權限（可能更改代幣規則）",
    "ton_whitelisted": "TON 官方白名單驗證通過",
}

# 信心分基線（無任何信號時的保守起點，不是 0 也不是 50）
_BASE_CONFIDENCE = 10
# 含任一高風險信號時的 confidence 下限（避免被安全信號抵消到過低）
_HIGH_RISK_CONFIDENCE_FLOOR = 65


def score_scam_confidence(signals: Dict[str, Any]) -> Dict[str, Any]:
    """把 _extract_risk_signals 的 dict 轉成可解釋信心分。

    Args:
        signals: _extract_risk_signals(info) 的回傳（含 high_risk / warnings /
            safeties / metrics / is_trusted / verdict）

    Returns:
        {
            "confidence": int (0-100),
            "verdict": str,             # 直接帶 signals["verdict"]
            "base": int,                # 基線
            "breakdown": [              # 加權明細（可解釋性核心）
                {"signal": str, "weight": int, "reason": str},
                ...
            ],
        }
    """
    breakdown: List[Dict[str, Any]] = []

    # 高風險信號
    for sig in signals.get("high_risk", []):
        w = _HIGH_RISK_WEIGHTS.get(sig, 0)
        if w:
            breakdown.append(
                {"signal": sig, "weight": w, "reason": _REASON_ZH.get(sig, sig)}
            )

    # 警告信號
    for sig in signals.get("warnings", []):
        w = _WARNING_WEIGHTS.get(sig, 0)
        if w:
            breakdown.append(
                {"signal": sig, "weight": w, "reason": _REASON_ZH.get(sig, sig)}
            )

    # 安全信號（負分抵扣）
    for sig in signals.get("safeties", []):
        w = _SAFETY_WEIGHTS.get(sig, 0)
        if w:
            breakdown.append(
                {"signal": sig, "weight": w, "reason": _REASON_ZH.get(sig, sig)}
            )

    raw = _BASE_CONFIDENCE + sum(b["weight"] for b in breakdown)
    confidence = max(0, min(100, raw))

    # 含任一高風險信號 → 套下限，避免致命風險被安全信號抵消到過低
    if signals.get("high_risk"):
        confidence = max(confidence, _HIGH_RISK_CONFIDENCE_FLOOR)

    return {
        "confidence": confidence,
        "verdict": signals.get("verdict", "insufficient"),
        "base": _BASE_CONFIDENCE,
        "breakdown": breakdown,
    }
