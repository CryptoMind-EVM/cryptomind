"""
可疑錢包追蹤系統 - 舉報管理 API
"""

import asyncio
import logging
import re as _re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from api.deps import get_current_user, get_optional_current_user
from api.middleware.rate_limit import limiter
from api.utils import run_sync
from core.config import TRUST_EVENT_RECOMPUTE_ENABLED
from core.onchain.addresses import (
    is_evm_address,
    ton_friendly_to_raw,
    ton_raw_to_friendly,
)
from core.orm.config_repo import config_repo
from core.orm.repositories import user_repo
from core.orm.scam_tracker_repo import scam_tracker_repo
from core.tools.crypto_modules import goplus as _goplus
from core.tools.crypto_modules import ton_safety as _ton_safety
from core.validators import (
    filter_sensitive_content,
    mask_wallet_address,
    sanitize_description,
)

from .models import ScamReportCreate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Scam Tracker - Reports"])


async def _recompute_trust_on_scam_hit(scam_wallet_address: str) -> None:
    """被檢舉的錢包若是平台使用者，立刻重算其 trust_score。

    2026-09-04：TRUST_EVENT_RECOMPUTE_ENABLED 自建立起就沒有任何模組讀它——
    「錢包被詐騙 DB 命中時立即重算」這條路從來沒有接上，實際只有每天 00:30
    的 cron 會重算。core/identity/scoring.py 的 docstring 早就列了
    reason="event_scam_hit"，等的就是這裡。

    - **不傳正規化過的地址**：scam_reports 存 raw（TON）／.upper()（其他），但
      user_id（TON 用戶即錢包地址）是原始寫法，轉過去就對不到人。
      recompute_user_trust 內部的 collect_scam_penalty 會自己正規化。
    - **先確認是平台使用者**：recompute_user_trust 對不存在的 user_id 仍會跑完
      所有 collector 並持久化，等於替非使用者建 trust 列。
    - **EVM 地址不是 user_id**：主人是 evm_0x…（登入身份）或綁了這個地址的
      tg_／g_ 帳號——用 find_user_ids_by_evm_address 找（綁定表／身份／trust 綁定欄位）。
    - **絕不影響檢舉本身**：任何失敗只記 log，報告已經寫進去了。
    """
    if not TRUST_EVENT_RECOMPUTE_ENABLED:
        return
    addr = (scam_wallet_address or "").strip()
    if not addr:
        return
    try:
        from core.identity.scoring import recompute_user_trust

        if is_evm_address(addr):
            from core.database.user import find_user_ids_by_evm_address

            owners = await run_sync(find_user_ids_by_evm_address, addr.lower())
            for owner_id in owners:
                await recompute_user_trust(owner_id, reason="event_scam_hit")
            if owners:
                logger.info(
                    "[scam-report] trust recomputed on scam hit: %s (%d owners)",
                    addr[:12],
                    len(owners),
                )
            return

        # 舊 TON 身份：user_id 即錢包地址
        owner = await user_repo.get_by_id(addr)
        if not owner:
            return
        await recompute_user_trust(
            addr, wallet_address=addr, reason="event_scam_hit"
        )
        logger.info("[scam-report] trust recomputed on scam hit: %s", addr[:12])
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scam-report] event recompute failed (non-fatal): %s", exc)


def _display_report(report: dict) -> dict:
    """DB 存 raw（0:…）給機器比對；回給人看的轉成 UQ…（non-bounceable，錢包慣用寫法）。

    只轉 raw：舊資料 upper() 過的 friendly 已經丟了大小寫，轉不回原地址，原樣回。
    """
    stored = report.get("scam_wallet_address") or ""
    friendly = ton_raw_to_friendly(stored) if ":" in stored else None
    return {**report, "scam_wallet_address": friendly} if friendly else report


async def _heal_legacy_ton_rows(address: str, reports: list[dict]) -> None:
    """查詢對到的舊 raw upper 列（0:ABC…）改寫成 raw（best-effort，回應送出後才跑）。

    - raw upper：hex 不分大小寫，無損，直接改。
    - friendly upper（EQ…／UQ…）**一律不改**：base64 大小寫有意義，upper 後同一串
      對得到大量 CRC 合法的**其他**地址（隨機翻大小寫幾萬次就湊得出一個），連鏈上
      active 都無法證明這筆舊資料就是查詢的這個帳戶——改錯了舉報會搬到別的錢包、
      真正的詐騙地址從此查不到。查詢本來就靠舊寫法對得到它，不需要改。
    - 撞 unique（raw 列已存在）由 repo 回 conflict：舊列留著，查詢兩列都回。
    """
    raw = ton_friendly_to_raw(address)
    if not raw:
        return
    try:
        for report in reports:
            stored = report.get("scam_wallet_address") or ""
            if stored == raw or stored != raw.upper():
                continue
            outcome = await scam_tracker_repo.heal_legacy_address(
                report.get("id"), stored, raw
            )
            logger.info(
                "[scam-report] legacy address #%s: %s", report.get("id"), outcome
            )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[scam-report] legacy address heal failed (non-fatal): %s", exc)


@router.get("", response_model=dict)
async def list_scam_reports(
    scam_type: Optional[str] = Query(None, description="詐騙類型篩選"),
    status: Optional[str] = Query(
        None, description="驗證狀態篩選 (pending/verified/disputed)"
    ),
    sort_by: str = Query(
        "latest", description="排序方式 (latest/most_voted/most_viewed)"
    ),
    limit: int = Query(20, ge=1, le=100, description="每頁數量"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """
    獲取舉報列表

    公開端點，所有用戶可查看。
    """
    try:
        reports = await scam_tracker_repo.get_reports(
            scam_type=scam_type,
            status=status,
            sort_by=sort_by,
            limit=limit,
            offset=offset,
        )

        reports = [_display_report(r) for r in reports]
        return {"success": True, "reports": reports, "count": len(reports)}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"List scam reports failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch report list, please try again later")


@router.get("/search", response_model=dict)
@limiter.limit("30/minute")
async def search_scam_wallet(
    request: Request,
    background_tasks: BackgroundTasks,
    wallet_address: str = Query(..., description="錢包地址"),
):
    """
    搜尋錢包是否被舉報

    公開端點，返回該錢包的舉報詳情（如果存在）。report 是最早的一筆，
    reports 是同一個錢包的全部舉報（舊資料可能 EQ／UQ 各一筆）。
    """
    try:
        reports = await scam_tracker_repo.find_reports_by_address(wallet_address)

        if reports:
            background_tasks.add_task(_heal_legacy_ton_rows, wallet_address, reports)
            shown = [_display_report(r) for r in reports]
            return {
                "success": True,
                "found": True,
                "report": shown[0],
                "reports": shown,
            }
        else:
            return {"success": True, "found": False, "message": "This wallet has not been reported"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Search scam wallet failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Search failed, please try again later")


@router.get("/config", response_model=dict)
async def get_scam_tracker_config():
    """
    獲取詐騙追蹤系統配置

    返回詐騙類型列表和相關配置。
    """
    try:
        scam_types = await config_repo.get_config("scam_types", [])

        return {
            "success": True,
            "scam_types": scam_types,
            "verification_threshold": await config_repo.get_config(
                "scam_verification_vote_threshold", 10
            ),
            "verification_approve_rate": await config_repo.get_config(
                "scam_verification_approve_rate", 0.7
            ),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get scam tracker config failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch config, please try again later")


# ── 地址健診 v0（design 2026-08-18）────────────────────────────────────────
# 公開、免登入：把社群舉報＋GoPlus（EVM）＋TonAPI verification（TON）合成一份
# 判定。fail-soft：單源掛不掉整體；資訊不完整判 caution 而非 no_red_flags。

_EVM_RE = _re.compile(r"^0x[0-9a-fA-F]{40}$")
_TON_FRIENDLY_RE = _re.compile(r"^[EUQ]Q[0-9A-Za-z_\-]{46}$")
_TON_RAW_RE = _re.compile(r"^0:[0-9a-fA-F]{64}$")


def _detect_family(address: str) -> Optional[str]:
    a = address.strip()
    if _EVM_RE.match(a):
        return "evm"
    if _TON_FRIENDLY_RE.match(a) or _TON_RAW_RE.match(a):
        return "ton"
    return None


@router.get("/check", response_model=dict)
@limiter.limit("30/minute")
async def address_checkup(
    request: Request,
    background_tasks: BackgroundTasks,
    address: str = Query(..., min_length=8, max_length=100),
):
    """轉帳前地址健診（公開端點；風險判定＋證據摘要，不含任何用戶資料）。"""
    addr = address.strip()
    family = _detect_family(addr)
    if not family:
        raise HTTPException(
            status_code=422,
            detail="Unsupported address format (expect EVM 0x… or TON EQ/UQ…/0:…)",
        )

    reasons: list[str] = []
    caution = False
    sources: dict = {"community": {"status": "ok", "found": False}}

    # ① 社群舉報資料庫（DB；fail-soft）
    try:
        reports = await scam_tracker_repo.find_reports_by_address(addr)
        if reports:
            background_tasks.add_task(_heal_legacy_ton_rows, addr, reports)
            # repo 的 dict 欄位是 id／verification_status（以前讀 report_id／status，
            # 永遠是 None，前端「查看舉報」連到 detail.html?id=）
            sources["community"] = {
                "status": "ok",
                "found": True,
                "report_id": reports[0].get("id"),
                "report_ids": [r.get("id") for r in reports],
                "status_label": reports[0].get("verification_status"),
            }
            reasons.append("community_report")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        sources["community"] = {"status": "error", "found": False}
        caution = True
        logger.info("[checkup] community source failed: %s", exc)

    # ② 家族別鏈上源（同步 httpx → to_thread；AGENTS.md 禁 sync I/O 直進 async）
    if family == "evm":
        try:
            raw = await asyncio.to_thread(_goplus.fetch_address_security_raw, addr)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raw = {"info": None, "error": str(exc)}
        flags = _goplus.goplus_malicious_flags(raw.get("info") or {})
        if raw.get("error"):
            # 公開端點：原始錯誤（可能含環境變數名、例外字串）只進 log
            logger.info("[checkup] goplus unavailable: %s", raw["error"])
            sources["goplus"] = {"status": "error", "detail": "unavailable"}
            caution = True
        else:
            sources["goplus"] = {
                "status": "ok",
                "flags": flags,
                "has_records": bool(raw.get("info")),
            }
            reasons.extend(flags)
        verdict = "high_risk" if (flags or sources["community"]["found"]) else (
            "caution" if caution else "no_red_flags"
        )
    else:
        def _ton_dict(s) -> dict:
            return {
                "verification": getattr(s, "verification", None),
                "symbol": getattr(s, "symbol", None),
                "name": getattr(s, "name", None),
                "holders_count": getattr(s, "holders_count", None),
                "has_admin": getattr(s, "has_admin", None),
                "exists": getattr(s, "exists", None),
                "signals": getattr(s, "signals", None) or {},
            }

        try:
            safety = await asyncio.to_thread(_ton_safety.assess_jetton_safety, addr)
            ton = _ton_dict(safety)
            if ton["verification"] == "blacklist":
                reasons.append("tonapi_blacklist")
            if ton["has_admin"]:
                reasons.append("jetton_admin_privilege")
                caution = True
            sources["tonapi"] = {"status": "ok", **ton}
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.info("[checkup] tonapi failed: %s", exc)
            sources["tonapi"] = {"status": "error", "detail": "unavailable"}
            caution = True
        high = ("tonapi_blacklist" in reasons) or sources["community"]["found"]
        verdict = "high_risk" if high else ("caution" if caution else "no_red_flags")

    return {
        "success": True,
        "address": addr,
        "family": family,
        "verdict": verdict,
        "reasons": reasons,
        "sources": sources,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def _daily_report_usage(user_id: str) -> tuple[int, int]:
    """(今日已舉報數, 每日上限)；上限用 legacy 同一個 system_config key。"""
    daily_limit = await config_repo.get_config("scam_report_daily_limit_pro", 5)
    used = await scam_tracker_repo.count_reports_today(user_id)
    return used, daily_limit


@router.get("/quota", response_model=dict)
@limiter.limit("60/minute")
async def get_report_quota(
    request: Request, current_user: dict = Depends(get_current_user)
):
    """今日剩餘舉報次數（submit 頁顯示；以前前端寫死 5）。要放在 /{report_id} 前面。"""
    try:
        used, daily_limit = await _daily_report_usage(current_user.get("user_id"))
        return {
            "success": True,
            "limit": daily_limit,
            "used": used,
            "remaining": max(0, daily_limit - used),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get scam report quota failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch quota, please try again later"
        )


@router.get("/{report_id}", response_model=dict)
async def get_scam_report_detail(
    report_id: int, current_user: Optional[dict] = Depends(get_optional_current_user)
):
    """
    獲取舉報詳情

    公開端點，如果提供 token 則包含用戶投票狀態。
    """
    try:
        user_id = current_user.get("user_id") if current_user else None

        report = await scam_tracker_repo.get_report_by_id(
            report_id, increment_view=True, viewer_user_id=user_id
        )

        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        return {"success": True, "report": _display_report(report)}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Get scam report detail failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch report details, please try again later")


@router.post("", response_model=dict)
@limiter.limit("5/minute")
async def create_new_scam_report(
    request: Request,
    req: ScamReportCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    提交新舉報

    僅 Premium 會員可使用。
    """
    try:
        user_id = current_user.get("user_id")

        # 驗證用戶是否存在
        user = await user_repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=401, detail="User not found or credentials have expired, please log in again"
            )

        # 以下三道關卡 legacy create_scam_report 都有，2026-03 ORM 遷移（8c11e5e）
        # 換成 repo.create_report 時沒搬過來（repo 註明由呼叫端負責）。
        # get_by_id 的 is_premium 已算進到期。
        if not user.get("is_premium"):
            raise HTTPException(
                status_code=403,
                detail={
                    "reason": "premium_membership_required",
                    "message": "Scam wallet reporting is available only to Premium members",
                },
            )

        # 檢查收過空白的版本（同 legacy）；存的仍是原文——詳情頁把 \n 轉 <br>
        content_check = filter_sensitive_content(sanitize_description(req.description))
        if not content_check["valid"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "content_validation_failed",
                    "message": (
                        "Description did not pass content review. Remove emails, "
                        "phone numbers, messaging handles and external links."
                    ),
                    "warnings": content_check["warnings"],
                    "codes": content_check.get("codes", []),
                },
            )

        used, daily_limit = await _daily_report_usage(user_id)
        if used >= daily_limit:
            raise HTTPException(
                status_code=429,
                detail={
                    "reason": "daily_limit_reached",
                    "message": f"Daily report limit reached ({used}/{daily_limit})",
                    "limit": daily_limit,
                    "used": used,
                },
            )

        result = await scam_tracker_repo.create_report(
            scam_wallet_address=req.scam_wallet_address,
            reporter_user_id=user_id,
            # 這欄會在公開的列表／詳情顯示——存遮罩後的值，不能存完整地址
            reporter_wallet_masked=mask_wallet_address(req.reporter_wallet_address),
            scam_type=req.scam_type,
            description=req.description,
            transaction_hash=req.transaction_hash,
        )

        if result.get("success"):
            await _recompute_trust_on_scam_hit(req.scam_wallet_address)
            return {
                "success": True,
                "report_id": result["report_id"],
                "message": "Report submitted successfully",
            }
        else:
            error = result.get("error")
            detail = result.get("detail", "")

            # 處理各種錯誤情況（premium／每日上限／內容過濾在上面就擋了）
            if error == "already_reported":
                existing_id = result.get("existing_report_id")
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "This wallet has already been reported",
                        # 前端讀 detail.message；物件沒有它會顯示 [object Object]
                        "message": "This wallet has already been reported",
                        "existing_report_id": existing_id,
                    },
                )
            elif error == "invalid_scam_wallet":
                raise HTTPException(
                    status_code=400, detail=f"Invalid suspicious wallet address: {detail}"
                )
            elif error == "invalid_reporter_wallet":
                raise HTTPException(
                    status_code=400, detail=f"Invalid reporter wallet address: {detail}"
                )
            elif error == "invalid_tx_hash":
                raise HTTPException(status_code=400, detail=f"Invalid transaction hash: {detail}")
            else:
                raise HTTPException(status_code=500, detail=f"Submission failed: {error}")

    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"Create scam report failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to submit report, please try again later")
