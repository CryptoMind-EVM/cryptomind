"""
Identity Trust Layer — Principal 支柱的可信身分聚合（可信 AI 黑客松）

問題
----
TON Connect 的 `ton_proof` 只證明「使用者控制這個錢包」，不證明「這是獨一無二的
真人」。對普惠金融「被信任、不被冒用」場景，缺的是 proof of personhood 與鏈上
信任信號。本層把多個獨立的信任信號聚合成一個 0-100 的 `trust_score`，供 Consent
Gate 做「高風險動作需較高信任度」的決策。

設計（對齊業界 personhood 機制）
--------------------------------
- **World ID (Worldcoin)**：生物辨識 proof-of-personhood（iris + ZK proof）。
  硬體 Orb 門檻高，但業界最夯的「真人獨一性」證明。
- **Gitcoin Passport**：軟體式 stamp 聚合（ENS、社群帳號、生物辨識可選），
  業界 sybil resistance 標準。
- **TON 生態 SBT / 鏈上行為**：錢包年齡、活動度、是否持有身分 SBT。
- **錢包所有權**：EVM SIWE 簽章（api/evm_verification.py；舊 ton_proof 已移除）。

本層不硬綁單一廠商。每個 signal 是一個 `TrustSignal` provider，可獨立插拔：
- 一個都沒設 → 只用錢包所有權 + 鏈上年齡（baseline 30-50 分）。
- 設了 World ID / Gitcoin → 加分到 70-100。
- Consent Gate 的 `min_trust_for_high_risk` 閾值決定哪些動作需要多強身分保證。

分數語意
--------
- 0-29：匿名 / 未知錢包（只簽了 ton_proof 但無任何歷史）。
- 30-59：已知錢包（有鏈上年齡或活動度）。
- 60-79：通過一個軟體式 personhood（如 Gitcoin Passport 閾值）。
- 80-100：通過強 personhood（如 World ID Orb 驗證）。

所有 signal 來源皆可選；缺失時該 signal 不計分（graceful degradation）。
無任何第三方相依 — 外部 personhood 驗證結果由 caller 以 `verified_stamps` 傳入
（caller 自行呼叫 World ID / Gitcoin API 後把 boolean 結果帶來）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from api.utils import logger

# ──────────────────────────────────────────────────────────────────────────────
# 分數閾值（Consent Gate 用）
# ──────────────────────────────────────────────────────────────────────────────
# 動作風險等級對應的最低信任分數。低於閾值時 Consent Gate 會要求額外驗證或拒絕。
MIN_TRUST_FOR_LOW = 0  # low-risk 動作任何人都能做
MIN_TRUST_FOR_MEDIUM = 20  # medium-risk 至少要已知錢包
MIN_TRUST_FOR_HIGH = 50  # high-risk 至少要有鏈上年齡 + 活動度（或更高）

# 每個 signal 的最高貢獻分（上限設計：單一 signal 不可能讓分數爆表，需多重保證）
# 有簽章證明過的錢包（EVM 登入／綁定、舊 TON 身份）就給的 baseline
_MAX_WALLET_OWNERSHIP = 30
_MAX_WALLET_AGE = 20  # 鏈上存在時間
_MAX_WALLET_ACTIVITY = 15  # 交易活動度
_MAX_SOFT_PERSONHOOD = 20  # Gitcoin Passport / 類似軟體 stamp
_MAX_STRONG_PERSONHOOD = 35  # World ID Orb / 類似生物辨識


@dataclass
class TrustSignal:
    """單一信任信號的評估結果。"""

    name: str  # signal 識別名（如 "wallet_ownership"、"world_id"）
    score: int  # 此 signal 貢獻的分數（已 cap 過）
    weight_applied: int  # 實際套用的權重上限（供 audit）
    detail: str = ""  # 人類可讀說明（會進 audit metadata）


@dataclass
class IdentityTrustAssessment:
    """完整身分信任評估結果。"""

    trust_score: int  # 0-100 聚合分數
    tier: str  # "anonymous" / "known_wallet" / "soft_verified" / "strong_verified"
    signals: List[TrustSignal] = field(default_factory=list)
    sufficient_for: List[str] = field(default_factory=list)  # 通過的風險等級 ["low","medium"]
    insufficient_for: List[str] = field(default_factory=list)  # 未通過的風險等級 ["high"]
    assessed_at: str = ""

    def to_audit_metadata(self) -> Dict[str, Any]:
        """轉成 audit log 的 metadata 結構（記錄「為什麼給這個分數」）。"""
        return {
            "trust_score": self.trust_score,
            "tier": self.tier,
            "signals": [
                {"name": s.name, "score": s.score, "detail": s.detail}
                for s in self.signals
            ],
            "sufficient_for": self.sufficient_for,
            "insufficient_for": self.insufficient_for,
            "assessed_at": self.assessed_at,
        }


def _cap(value: int, max_value: int) -> int:
    """限制單一 signal 貢獻不超過上限，且不為負。"""
    return max(0, min(value, max_value))


def _wallet_age_score(wallet_first_active: Optional[datetime]) -> int:
    """錢包年齡貢獻分。越老越可信（降低 sybil / 農場號風險）。"""
    if wallet_first_active is None:
        return 0
    now = datetime.now(timezone.utc)
    if wallet_first_active.tzinfo is None:
        wallet_first_active = wallet_first_active.replace(tzinfo=timezone.utc)
    age_days = max(0, (now - wallet_first_active).days)
    # 線性增長到 365 天滿分（_MAX_WALLET_AGE）。超過不再加。
    return _cap(int(_MAX_WALLET_AGE * min(1.0, age_days / 365)), _MAX_WALLET_AGE)


def _wallet_activity_score(tx_count: Optional[int]) -> int:
    """錢包活動度貢獻分。有實際使用痕跡比空農場號可信。"""
    if not tx_count or tx_count <= 0:
        return 0
    # 50 筆交易滿分（_MAX_WALLET_ACTIVITY）。
    return _cap(int(_MAX_WALLET_ACTIVITY * min(1.0, tx_count / 50)), _MAX_WALLET_ACTIVITY)


def _stamp_score(stamps: Dict[str, bool], soft_keys: List[str], strong_keys: List[str]) -> tuple[int, int, List[str]]:
    """聚合 personhood stamps。回傳 (soft_score, strong_score, hit_stamps)。"""
    soft = 0
    strong = 0
    hits: List[str] = []
    for key, ok in (stamps or {}).items():
        if not ok:
            continue
        hits.append(key)
        if key in strong_keys:
            strong = _MAX_STRONG_PERSONHOOD
        elif key in soft_keys:
            soft = _MAX_SOFT_PERSONHOOD
    return soft, strong, hits


def assess_identity_trust(
    *,
    wallet_verified: bool,
    wallet_first_active: Optional[datetime] = None,
    wallet_tx_count: Optional[int] = None,
    verified_stamps: Optional[Dict[str, bool]] = None,
) -> IdentityTrustAssessment:
    """評估使用者的身分信任分數（0-100）。

    Args:
        wallet_verified: 是否有簽章證明過的錢包（錢包所有權）。這是 baseline。
        wallet_first_active: 錢包首次活躍時間（鏈上年齡）。None = 未知。
        wallet_tx_count: 錢包歷史交易數（活動度）。None = 未知。
        verified_stamps: 外部 personhood stamp 的驗證結果（caller 已驗過）。
            key 是 stamp 名（如 "world_id"、"gitcoin_passport"、"ton_sbt_identity"），
            value 是 True/False（是否通過）。本函式不呼叫外部 API。

    Returns:
        IdentityTrustAssessment：聚合後的信任評估，含每個 signal 的明細。
        進 audit log（to_audit_metadata）記錄「為什麼給這個分數」。

    設計取捨：
        - graceful degradation：任何 signal 缺失都不報錯，只是不計分。
        - 多重保證：單一 signal 上限設計，要拿高分需多重驗證。
        - 無第三方相依：外部驗證結果由 caller 帶入，本層只做聚合。
    """
    signals: List[TrustSignal] = []
    total = 0

    # 1. 錢包所有權（baseline）— EVM 登入／綁定（SIWE）或舊 TON 身份（ton_proof）。
    #    Telegram／Google-only 帳號沒有 → 0 分（caller 依身份與 user_wallets 判定）。
    ownership_score = _MAX_WALLET_OWNERSHIP if wallet_verified else 0
    signals.append(TrustSignal(
        name="wallet_ownership",
        score=ownership_score,
        weight_applied=_MAX_WALLET_OWNERSHIP,
        detail="wallet verified" if wallet_verified else "no wallet proof",
    ))
    total += ownership_score

    if not wallet_verified:
        # 連錢包都沒驗 → 直接 anonymous，不計其他 signal（避免無意義加分）
        return _build_assessment(0, "anonymous", signals)

    # 2. 鏈上年齡
    age_score = _wallet_age_score(wallet_first_active)
    signals.append(TrustSignal(
        name="wallet_age",
        score=age_score,
        weight_applied=_MAX_WALLET_AGE,
        detail=f"{(datetime.now(timezone.utc) - wallet_first_active).days} days"
        if wallet_first_active else "missing_history",
    ))
    total += age_score

    # 3. 活動度
    activity_score = _wallet_activity_score(wallet_tx_count)
    signals.append(TrustSignal(
        name="wallet_activity",
        score=activity_score,
        weight_applied=_MAX_WALLET_ACTIVITY,
        detail=f"{wallet_tx_count} txs" if wallet_tx_count else "missing_history",
    ))
    total += activity_score

    # 4. 外部 personhood stamps（可插拔）
    soft_keys = ["gitcoin_passport", "ton_sbt_identity", "ens", "brightid"]
    strong_keys = ["world_id", "worldcoin_orb", "proof_of_humanity"]
    soft_score, strong_score, hits = _stamp_score(
        verified_stamps or {}, soft_keys, strong_keys
    )
    if soft_score > 0:
        signals.append(TrustSignal(
            name="soft_personhood",
            score=soft_score,
            weight_applied=_MAX_SOFT_PERSONHOOD,
            detail=f"stamps: {hits}",
        ))
        total += soft_score
    if strong_score > 0:
        signals.append(TrustSignal(
            name="strong_personhood",
            score=strong_score,
            weight_applied=_MAX_STRONG_PERSONHOOD,
            detail=f"stamps: {hits}",
        ))
        total += strong_score

    # 5. tier 判定（分數 → 語意 tier）
    if total >= 80:
        tier = "strong_verified"
    elif total >= 60:
        tier = "soft_verified"
    elif total >= 30:
        tier = "known_wallet"
    else:
        tier = "anonymous"

    return _build_assessment(min(100, total), tier, signals)


def _build_assessment(
    score: int, tier: str, signals: List[TrustSignal]
) -> IdentityTrustAssessment:
    """組裝 assessment + 計算 sufficient_for / insufficient_for。"""
    sufficient: List[str] = []
    insufficient: List[str] = []
    thresholds = [
        ("low", MIN_TRUST_FOR_LOW),
        ("medium", MIN_TRUST_FOR_MEDIUM),
        ("high", MIN_TRUST_FOR_HIGH),
    ]
    for level, threshold in thresholds:
        if score >= threshold:
            sufficient.append(level)
        else:
            insufficient.append(level)

    return IdentityTrustAssessment(
        trust_score=score,
        tier=tier,
        signals=signals,
        sufficient_for=sufficient,
        insufficient_for=insufficient,
        assessed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def meets_risk_requirement(assessment: IdentityTrustAssessment, risk_level: str) -> bool:
    """判斷此身分信任評估是否滿足指定風險等級的要求。"""
    threshold_map = {
        "low": MIN_TRUST_FOR_LOW,
        "medium": MIN_TRUST_FOR_MEDIUM,
        "high": MIN_TRUST_FOR_HIGH,
    }
    threshold = threshold_map.get(risk_level, 0)
    meets = assessment.trust_score >= threshold
    if not meets:
        logger.info(
            "[IdentityTrust] trust_score %d < threshold %d for risk_level %s (tier=%s)",
            assessment.trust_score, threshold, risk_level, assessment.tier,
        )
    return meets


__all__ = [
    "IdentityTrustAssessment",
    "TrustSignal",
    "assess_identity_trust",
    "meets_risk_requirement",
    "MIN_TRUST_FOR_LOW",
    "MIN_TRUST_FOR_MEDIUM",
    "MIN_TRUST_FOR_HIGH",
]
