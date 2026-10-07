"""Trust Score API — 自己看明細 / 別人看徽章

對應 design doc 的「分層揭露」：
    - GET /api/trust/score          → 自己看完整明細（診斷面板用）
    - GET /api/trust/tier/{user_id} → 別人看徽章等級（只回 tier + label，不洩漏分數明細）
    - POST /api/trust/recompute     → 觸發立即重算（給 cron / 事件觸發用，需 auth）

徽章 tier 映射（design doc Tier 0-4，學 Discourse TL）：
    anonymous      → Tier 0 新人     (score 0-29)
    known_wallet   → Tier 1 基本     (score 30-59)
    soft_verified  → Tier 2 成員     (score 60-79)
    strong_verified→ Tier 3 常客/領袖 (score 80-100)

Phase A0：徽章查詢已就緒，但 UI 尚未外顯（等 forum/friends 開放，design doc 軌道 B1）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.utils import logger as api_logger
from core.config import TRUST_EVM_BINDING_ENABLED, TRUST_SCORE_ENABLED
from core.database.user import (
    clear_user_evm_address,
    get_user_evm_address,
    is_evm_address_bound,
    set_user_evm_address,
)
from core.identity.evm_bind import (
    build_sign_message,
    generate_evm_bind_payload,
    normalize_evm_address,
    verify_evm_signature,
)
from core.identity.scoring import (
    get_latest_trust_score,
    get_user_trust_tier,
    recompute_user_trust,
)

router = APIRouter(prefix="/api/trust", tags=["Trust Score"])
_logger = logging.getLogger(__name__)

# 語意 tier → 外顯徽章 tier（design doc Tier 0-4）。
# 註：A0 階段只到 Tier 3（strong_verified）；Tier 4 Leader 需手動指定，B2 才開。
_TIER_BADGE = {
    "anonymous": {"badge_tier": 0, "label_en": "New", "label_zh": "新人"},
    "known_wallet": {"badge_tier": 1, "label_en": "Basic", "label_zh": "基本"},
    "soft_verified": {"badge_tier": 2, "label_en": "Member", "label_zh": "成員"},
    "strong_verified": {"badge_tier": 3, "label_en": "Regular", "label_zh": "常客"},
}


class RecomputeResponse(BaseModel):
    success: bool
    trust_score: int | None = None
    tier: str | None = None


def _service_unavailable() -> None:
    raise HTTPException(status_code=503, detail="Trust score feature is disabled")


def _breakdown_has_old_unknown(breakdown: Dict[str, Any]) -> bool:
    """偵測舊版 breakdown:signal detail 曾被寫成 "unknown"(7/29-8/9 版本缺陷)。

    新版(8/9 後)用 "missing_history" / 實際天數,前端可直接翻譯。
    舊資料的 "unknown" 前端也能顯示(有 map),但讀到時順手重算一次,
    讓 DB 升級成乾淨格式(不影響分數——recompute 是冪等的)。
    """
    if not breakdown:
        return False
    base = breakdown.get("base_assessment", {})
    for s in base.get("signals", []) or []:
        if s.get("detail") == "unknown":
            return True
    return False


def _breakdown_evm_binding_drift(latest: Dict[str, Any], bound_now: Optional[str]) -> bool:
    """breakdown 記錄的 EVM 地址與 users.evm_address 不一致。

    來源：登入 auto-bind（evm_login）、手動綁定／解綁、換綁——breakdown
    是重算時的快照，綁定變了它不會自己動。讀到漂移就重算一次讓它自癒
    （冪等，重算後兩邊一致就不會再觸發）。
    """
    passport = (latest.get("breakdown") or {}).get("passport") or {}
    return passport.get("evm_address") != bound_now


@router.get("/score")
@limiter.limit("30/minute")
async def get_my_trust_score(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """自己看完整 trust_score 明細（診斷面板用）。含「如何提升」建議。"""
    if not TRUST_SCORE_ENABLED:
        _service_unavailable()
    user_id = current_user["user_id"]
    latest = get_latest_trust_score(user_id)
    if latest is None:
        # 還沒算過——現場算一次（首次造訪自動初始化）。
        result = await recompute_user_trust(user_id, reason="first_access")
        if result is None:
            raise HTTPException(status_code=500, detail="Unable to compute trust score")
        latest = get_latest_trust_score(user_id)
        if latest is None:
            raise HTTPException(status_code=500, detail="Trust score unavailable")
    elif _breakdown_has_old_unknown(latest.get("breakdown")):
        # 舊版後端(7/29-8/9)在無鏈上紀錄時把 signal detail 寫成 "unknown"。
        # 讀到這種資料就自動重算一次,讓 DB 也升級成新格式(前端已能翻譯,
        # 但 DB 一致性更好——之後 audit / 其他消費方都拿到乾淨資料)。
        result = await recompute_user_trust(user_id, reason="legacy_unknown_repair")
        if result is not None:
            latest = get_latest_trust_score(user_id) or latest
    elif TRUST_EVM_BINDING_ENABLED and _breakdown_evm_binding_drift(
        latest, get_user_evm_address(user_id)
    ):
        # 綁定狀態與 breakdown 快照脫鉤（登入 auto-bind／手動綁解／換綁）。
        # 惰性重算：auto-bind 不在登入路徑付出重算延遲，分數在造訪本頁時刷新。
        result = await recompute_user_trust(user_id, reason="evm_binding_changed")
        if result is not None:
            latest = get_latest_trust_score(user_id) or latest

    badge = _TIER_BADGE.get(latest["tier"], _TIER_BADGE["anonymous"])
    return {
        "success": True,
        "trust_score": latest["trust_score"],
        "tier": latest["tier"],
        "badge": badge,
        "breakdown": latest["breakdown"],
        "computed_at": latest["computed_at"],
        "recompute_reason": latest["recompute_reason"],
        "next_steps": _suggest_next_steps(latest["breakdown"]),
    }


@router.get("/tier/{user_id}")
@limiter.limit("60/minute")
async def get_user_trust_badge(
    request: Request,
    user_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """別人看徽章等級（分層揭露：只回 tier + label，不洩漏分數明細）。"""
    if not TRUST_SCORE_ENABLED:
        _service_unavailable()
    tier = get_user_trust_tier(user_id)
    badge = _TIER_BADGE.get(tier, _TIER_BADGE["anonymous"])
    return {
        "success": True,
        "user_id": user_id,
        "tier": tier,
        "badge": badge,
    }


@router.post("/recompute", response_model=RecomputeResponse)
@limiter.limit("5/minute")
async def trigger_recompute(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> RecomputeResponse:
    """手動觸發自己的 trust_score 重算（測試 / 診斷用，嚴格 rate limit）。"""
    if not TRUST_SCORE_ENABLED:
        _service_unavailable()
    user_id = current_user["user_id"]
    try:
        result = await recompute_user_trust(user_id, reason="manual")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        api_logger.error("[trust] recompute failed for %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail="Recompute failed") from exc
    if result is None:
        return RecomputeResponse(success=False)
    return RecomputeResponse(
        success=True, trust_score=result["trust_score"], tier=result["tier"]
    )


# ──────────────────────────────────────────────────────────────────────────────
# EVM 地址綁定（Human Passport 訊號啟用）
# ──────────────────────────────────────────────────────────────────────────────


class EvmBindRequest(BaseModel):
    evm_address: str = Field(..., min_length=42, max_length=42)
    signature: str = Field(..., min_length=10)
    payload: str = Field(..., min_length=10)


class EvmBindResponse(BaseModel):
    success: bool
    evm_address: Optional[str] = None
    reason: Optional[str] = None


def _evm_binding_disabled() -> None:
    raise HTTPException(status_code=503, detail="EVM address binding is disabled")


@router.get("/evm/nonce")
@limiter.limit("10/minute")
async def get_evm_bind_nonce(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    """產生一次性 nonce payload（給前端組 personal_sign 訊息用）。"""
    if not (TRUST_SCORE_ENABLED and TRUST_EVM_BINDING_ENABLED):
        _evm_binding_disabled()
    payload = generate_evm_bind_payload()
    message = build_sign_message(payload)
    return {
        "success": True,
        "payload": payload,
        "message": message,
        "current_evm_address": get_user_evm_address(current_user["user_id"]),
    }


@router.post("/evm/bind", response_model=EvmBindResponse)
@limiter.limit("5/minute")
async def bind_evm_address(
    request: Request,
    body: EvmBindRequest,
    current_user: dict = Depends(get_current_user),
) -> EvmBindResponse:
    """綁定 EVM 地址（需 personal_sign 簽章證明擁有權）。"""
    if not (TRUST_SCORE_ENABLED and TRUST_EVM_BINDING_ENABLED):
        _evm_binding_disabled()
    user_id = current_user["user_id"]

    evm = normalize_evm_address(body.evm_address)
    if evm is None:
        raise HTTPException(status_code=400, detail="Invalid EVM address format")

    # 簽章驗證——核心防偽
    if not verify_evm_signature(evm_address=evm, signature=body.signature, payload=body.payload):
        raise HTTPException(status_code=403, detail="Signature verification failed")

    # 一地址只能綁一 user（防蹭分）
    if is_evm_address_bound(evm, exclude_user_id=user_id):
        raise HTTPException(status_code=409, detail="This EVM address is already bound to another account")

    ok, reason = set_user_evm_address(user_id, evm)
    if not ok:
        if reason == "duplicate":
            raise HTTPException(status_code=409, detail="EVM address already bound")
        raise HTTPException(status_code=500, detail="Failed to bind EVM address")

    api_logger.info("[trust] user %s bound EVM address %s", user_id, evm)
    return EvmBindResponse(success=True, evm_address=evm, reason="ok")


@router.delete("/evm/unbind", response_model=EvmBindResponse)
@limiter.limit("5/minute")
async def unbind_evm_address(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> EvmBindResponse:
    """解綁 EVM 地址（Passport 訊號隨之消失，分數下降）。"""
    if not (TRUST_SCORE_ENABLED and TRUST_EVM_BINDING_ENABLED):
        _evm_binding_disabled()
    user_id = current_user["user_id"]
    ok, reason = clear_user_evm_address(user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to unbind EVM address")
    api_logger.info("[trust] user %s unbound EVM address", user_id)
    return EvmBindResponse(success=True, reason=reason)


def _suggest_next_steps(breakdown: Dict[str, Any]) -> list[str]:
    """根據 breakdown 給「如何提升」建議（診斷面板的留存鉤子）。"""
    steps: list[str] = []
    if not breakdown:
        return steps
    onchain = breakdown.get("onchain", {})
    passport = breakdown.get("passport", {})
    scam = breakdown.get("scam_penalty", {})

    # 訊號缺失 → 建議補。看 TON＋EVM 合併後的資歷（EVM 使用者沒有 TON 訊號）；
    # 舊 breakdown 沒有 merged 欄位時退回只看 TON。
    first_active = breakdown.get("merged_first_active", onchain.get("first_active"))
    if first_active is None:
        steps.append("onchain_history_missing")  # 錢包太新/無歷史，無法立刻解，誠實標記
    if passport.get("reason") in ("passport_not_configured", "passport_scaffold_not_implemented"):
        steps.append("verify_with_human_passport")  # 可操作：去驗 Passport
    if scam.get("penalty", 0) > 0:
        steps.append("wallet_flagged_in_scam_db")  # 申訴途徑（governance）
    return steps


__all__ = ["router"]
