"""Canonical Free/Premium entitlement resolver.

單一權益來源（design 2026-08-13 §9.3）。錢包監測、安全與付款流程一律透過
本模組查詢使用者能做什麼——router、cron、前端不得各自複製 tier 規則。

建構於既有會員模型之上：
- ``core.database.tools.normalize_membership_tier`` 把 legacy 名稱收斂成 free/premium。
- ``core.database.user.get_user_membership`` 回傳
  ``{tier, expires_at, is_premium, is_expired}``，其中
  ``is_premium`` 表示「tier == premium」，``is_expired`` 表示已過期。

關鍵不變量（fail-safe）：
- **Active premium = is_premium AND NOT is_expired**。過期的 premium 退化成 free
  權益——沒有有效會員就沒有付費能力（對齊 design §15.2 fail-closed）。
- 以 server-side membership 為唯一來源；client body 宣告的 tier 永遠不被信任。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from core.database.tools import normalize_membership_tier


@dataclass(frozen=True)
class Entitlement:
    """使用者當下能力的不可變快照。"""

    tier: str  # 收斂後的有效 tier："free" | "premium"
    is_premium: bool  # True 僅當 premium 且未過期（即 active premium）
    is_expired: bool
    # 錢包／安全能力（design §5.2 entitlement matrix）
    wallet_snapshot_count: int  # 主動觸發的安全快照數
    scheduled_wallets: int  # 背景排程監測的錢包額度
    telegram_alerts: bool
    safety_history_days: int  # 0 = 無歷史查詢
    weekly_summary: bool

    @property
    def can_use_scheduled_monitoring(self) -> bool:
        """是否能啟用背景排程監測。"""
        return self.is_premium and self.scheduled_wallets > 0


# 單一 canonical 權益表（design §9.3）。新增／調整權益只改這裡。
_ENTITLEMENT_TABLE: dict[str, dict[str, Any]] = {
    "free": dict(
        wallet_snapshot_count=1,
        scheduled_wallets=0,
        telegram_alerts=False,
        safety_history_days=0,
        weekly_summary=False,
    ),
    "premium": dict(
        wallet_snapshot_count=5,
        scheduled_wallets=5,
        telegram_alerts=True,
        safety_history_days=90,
        weekly_summary=True,
    ),
}


def _effective_tier(tier: Optional[str], is_expired: bool) -> str:
    """過期的 premium 退化成 free；其餘照 normalize 結果。"""
    normalized = normalize_membership_tier(tier)
    if normalized == "premium" and is_expired:
        return "free"
    return normalized


def resolve_entitlement(
    *,
    tier: Optional[str] = None,
    is_expired: bool = False,
    membership: Optional[dict[str, Any]] = None,
) -> Entitlement:
    """由 membership dict（優先）或顯式 tier + is_expired 解析權益。

    Args:
        membership: ``get_user_membership`` 的回傳 dict；提供時忽略 tier/is_expired
            參數，改由 dict 內的欄位決定。
        tier / is_expired: 當無 membership dict 時的顯式輸入。

    過期的 premium 使用者取得 FREE 權益（fail-safe）。client 傳入的 tier 不被
    信任——cron、router 必須傳 server-side 查得的 membership。
    """
    if membership is not None:
        tier = membership.get("tier")
        is_expired = bool(membership.get("is_expired", False))

    effective = _effective_tier(tier, is_expired)
    table = _ENTITLEMENT_TABLE[effective]
    return Entitlement(
        tier=effective,
        is_premium=(effective == "premium"),
        is_expired=bool(is_expired),
        wallet_snapshot_count=table["wallet_snapshot_count"],
        scheduled_wallets=table["scheduled_wallets"],
        telegram_alerts=table["telegram_alerts"],
        safety_history_days=table["safety_history_days"],
        weekly_summary=table["weekly_summary"],
    )


def entitlement_summary(ent: Entitlement) -> dict:
    """前端顯示 + upgrade 判斷用的精簡權益摘要（供 router 共用，單一來源）。"""
    return {
        "tier": ent.tier,
        "is_premium": ent.is_premium,
        "can_use_scheduled_monitoring": ent.can_use_scheduled_monitoring,
        "scheduled_wallets": ent.scheduled_wallets,
        "wallet_snapshot_count": ent.wallet_snapshot_count,
        "telegram_alerts": ent.telegram_alerts,
        "weekly_summary": ent.weekly_summary,
    }


def resolve_entitlement_for_user(user_id: str) -> Entitlement:
    """便利載入器：由 user_id 查會員後解析權益。

    內部呼叫 sync DB（``get_user_membership``）；async 呼叫端需自行包
    ``run_sync``。``user_id`` 為空或查無使用者皆回傳 free 權益（fail-safe，
    不誤放行付費能力）。
    """
    if not user_id:
        return resolve_entitlement(tier="free")
    # 延遲 import 避免潛在循環。
    from core.database.user import get_user_membership

    try:
        membership = get_user_membership(user_id)
    except Exception:
        # DB 不可用時 fail-safe：不假設任何付費能力。
        return resolve_entitlement(tier="free")
    return resolve_entitlement(membership=membership)
