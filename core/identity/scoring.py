"""
Trust Score 編排層——把分散訊號採集 → 既有 assess_identity_trust → 持久化

分層（對應 design doc 的「評分層 + 訊號採集層」）：
    SignalCollector         →  採集單一訊號（鏈上 / 平台活動 / 詐騙 DB / Passport）
         │
         ▼
    recompute_user_trust()  →  聚合所有訊號 → assess_identity_trust（既有）
         │                     → 套用 penalty → 持久化到 user_trust_scores + users 快取
         ▼
    assess_identity_trust   →  純計算（已存在於 core/identity/trust.py）

設計取捨：
    - 重用 assess_identity_trust，不重寫計算邏輯（它已是 audit-grade）。
    - 本層只負責「採集 + 編排 + 持久化」——可獨立測試（collectors 可 mock）。
    - graceful degradation：任何 collector 失敗都不中斷，只是該訊號缺失不計分。
    - penalty 是「事後扣分」（從 assess_identity_trust 的結果扣），不進 assess 的
      signal 結構——因為 assess 的設計是「加分聚合」，penalty 是不同概念。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.config import (
    HUMAN_PASSPORT_API_KEY,
    HUMAN_PASSPORT_SCORER_ID,
    HUMAN_PASSPORT_SCORER_THRESHOLD,
    IS_TESTNET,
    TRUST_INACTIVITY_DECAY_DAYS,
)
from core.database.connection import get_connection
from core.identity.onchain_signals import fetch_wallet_onchain_signals
from core.identity.trust import (
    IdentityTrustAssessment,
    assess_identity_trust,
)
from core.onchain.addresses import (
    is_ton_address,
    scam_address_key,
    scam_address_lookup_keys,
)

logger = logging.getLogger(__name__)

# Penalty 上限——單一錢包被詐騙 DB 命中就扣到此值（設計 doc：命中即扣，規則非加權）。
_SCAM_PENALTY = 100

# 平台活動度（activity_score）的滿分上限。對齊 trust.py 的 _MAX_WALLET_ACTIVITY
# 同量級（15）但略高——平台活動比單純鏈上 tx 更能反映「這人還在不在用」。
_MAX_ACTIVITY = 20

# 活動度指數衰減的半衰期（天）。參考 Discourse TL3 的 100 天 rolling window，
# 但金融 app 期望登入頻率更高，取 1/3。意義：30 天沒登入 → activity 分數掉一半。
# 公式來源：學術界信任衰減共識 T(t)=T₀·e^(−kt)，t_half=ln(2)/k（見 design doc 引用）。
_ACTIVITY_HALF_LIFE_DAYS = 30
_ACTIVITY_DECAY_K = math.log(2) / _ACTIVITY_HALF_LIFE_DAYS

# EVM 鏈上資歷：產品鏈（Base）為主；以太坊主網訊號既有實作、30 天快取，便宜就留著
# （很多 Base 錢包的資歷其實在主網）。
_ETHEREUM_MAINNET_CHAIN_ID = 1


def platform_evm_chain_id() -> int:
    """產品用的 EVM 鏈：Base（測試網 Base Sepolia）——與 SIWE／USDC 付款同一個網路設定。"""
    return 84532 if IS_TESTNET else 8453


async def collect_onchain_signals(wallet_address: str) -> Dict[str, Any]:
    """採集 TON 鏈上年齡 + 活動度（onchain_signals.py）。graceful。"""
    try:
        result = await fetch_wallet_onchain_signals(wallet_address)
        if "error" in result:
            return {"_error": result["error"]}
        return {
            "first_active": result.get("first_active"),
            "tx_count": result.get("tx_count"),
            "is_active": result.get("is_active"),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — collector 隔離，不中斷編排
        logger.warning("[trust] onchain collector failed for %s: %s", wallet_address, exc)
        return {"_error": str(exc)}


async def collect_evm_onchain_signals(
    evm_address: str, chain_id: Optional[int] = None, user_id: Optional[str] = None
) -> Dict[str, Any]:
    """採集 EVM 鏈上年齡 + 活動度（路線 D + 模式 B）。graceful——無地址/無 key/失敗 → 不計分。

    模式 B：取使用者的 BYOK key（顯式傳 user_id）；無 BYOK 時 fallback 服務 key。
    docs/plans/2026-08-11-trust-evm-onchain-signals-design.md
    """
    if not evm_address:
        return {"_skipped": "no_evm_address"}
    if chain_id is None:
        chain_id = platform_evm_chain_id()
    # 模式 B：取使用者的 Etherscan BYOK key（cron/recompute 無 request context，顯式傳）
    api_key = None
    if user_id:
        try:
            from core.tools.key_resolver import resolve_tool_key

            api_key = resolve_tool_key(
                "etherscan",
                official_env="ETHERSCAN_SERVICE_API_KEY",  # 使用者沒 BYOK 時 fallback 服務 key
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
    try:
        from core.identity.onchain_signals import fetch_evm_onchain_signals

        result = await fetch_evm_onchain_signals(evm_address, chain_id, api_key=api_key)
        if "error" in result:
            return {"_error": result["error"]}
        return {
            "first_active": result.get("first_active"),
            "tx_count": result.get("tx_count"),
            "is_active": result.get("is_active"),
        }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trust] EVM onchain collector failed for %s: %s", evm_address[:12], exc)
        return {"_error": str(exc)}


async def _noop_evm() -> Dict[str, Any]:
    """無 EVM 地址時的 placeholder（讓 asyncio.gather 各路對稱）。"""
    return {"_skipped": "no_evm_address"}


async def _noop_ton() -> Dict[str, Any]:
    """不是舊 TON 身份時的 placeholder（不拿 evm_…／tg_… 去打 TonAPI）。"""
    return {"_skipped": "no_ton_address"}


def collect_scam_penalty(wallet_address: str) -> Dict[str, Any]:
    """採集詐騙 DB penalty（直接查 scam_reports，inline query 避免 import 鏈）。graceful。

    用 scam_reports 命中當 penalty 訊號——客觀來源（非主觀檢舉），符合 design doc。
    直接 inline 查詢而非 import core.database.scam_tracker，因為 scam_tracker 會拖出
    core.audit + core.validators 重依賴，cron-worker-mic image 沒裝。
    """
    addr = (wallet_address or "").strip()
    if not addr:
        return {"penalty": 0, "reason": "no_address"}
    conn = get_connection()
    try:
        with conn.cursor() as c:
            # TON 存 raw、舊資料是 upper() 過的各種寫法——全部一起比
            c.execute(
                "SELECT 1 FROM scam_reports WHERE scam_wallet_address = ANY(%s) LIMIT 1",
                (scam_address_lookup_keys(addr),),
            )
            hit = c.fetchone() is not None
        return {"penalty": _SCAM_PENALTY if hit else 0, "reason": "scam_report_hit" if hit else "clean"}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trust] scam penalty collector failed for %s: %s", wallet_address, exc)
        return {"penalty": 0, "reason": f"error: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _penalty_addresses(
    user_id: str, ton_address: Optional[str], evm_address: Optional[str]
) -> list[str]:
    """詐騙扣分要查的地址：主要地址＋帳號連結過的每一個（去重）。

    只查主要地址的話，換綁就能洗掉扣分（#876 review MEDIUM-2）：
    find_user_ids_by_evm_address 從舊的 users.evm_address = A 找到主人，
    主要地址卻是新綁的 B。連結清單查不到（DB 錯誤）就退回只查主要地址。
    """
    candidates = [ton_address, evm_address]
    try:
        from core.database.user import get_linked_wallet_addresses

        candidates += get_linked_wallet_addresses(user_id)
    except Exception as exc:  # noqa: BLE001 — 查不到就只查主要地址，不中斷重算
        logger.warning("[trust] linked wallets lookup failed for %s: %s", user_id, exc)
    seen: set = set()
    addresses: list[str] = []
    for addr in candidates:
        if not addr:
            continue
        key = scam_address_key(addr)
        if key not in seen:
            seen.add(key)
            addresses.append(addr)
    return addresses


async def collect_passport_stamps(user_id: str) -> Dict[str, Any]:
    """採集 Human Passport stamps（用綁定的 EVM 地址查 Scorer API）。

    流程：讀 users.evm_address → 有綁定 → fetch_passport_score(evm) → 分數
    → 漸進計分（score/100 × MAX）→ 餵給 assess_identity_trust。

    漸進計分設計（修掉「二選一」缺陷）：
        Passport 分數本身是 0-100 連續值。之前用「≥20 → +20 / <20 → +0」的
        開關式，會：(a) 19 分跟 0 分一樣對待（差一點過門檻的人被當完全沒驗證），
        (b) 20 分跟 99 分一樣對待（努力養 stamps 的人白費）。改成漸進後：
            passport_score=19 → +3.8 分（有合理小加分）
            passport_score=80 → +16 分（努力看得見）
        `gitcoin_passport` stamp 保留為 True（表示已通過平台門檻，供 audit）。

    graceful：未綁 EVM / key 未設 / API 失敗 → 不計分。
    """
    if not (HUMAN_PASSPORT_SCORER_ID and HUMAN_PASSPORT_API_KEY):
        return {"stamps": {}, "reason": "passport_not_configured"}

    # 延遲 import 避免循環
    from core.database.user import get_user_evm_address
    from core.identity.human_passport import fetch_passport_score

    evm_address = get_user_evm_address(user_id)
    if not evm_address:
        return {"stamps": {}, "reason": "evm_address_not_bound"}

    score = await fetch_passport_score(evm_address)
    if score is None:
        return {"stamps": {}, "reason": "passport_api_unavailable", "evm_address": evm_address}

    # 漸進計分：score/100 × 20（soft_personhood 上限）。score 是 0-100 的連續值。
    graduated = round((score / 100.0) * 20.0, 2)
    passed_threshold = score >= HUMAN_PASSPORT_SCORER_THRESHOLD
    return {
        "stamps": {"gitcoin_passport": True} if passed_threshold else {},
        "reason": "verified" if passed_threshold else "below_threshold",
        "passport_score": score,
        "graduated_bonus": graduated,
        "evm_address": evm_address,
    }


def collect_activity_signals(user_id: str) -> Dict[str, Any]:
    """採集平台活動度訊號（讀 users.last_active_at，算指數衰減分數）。graceful。

    衰減公式：activity_score = _MAX_ACTIVITY × e^(−k · inactive_days)
      - 半衰期 30 天（_ACTIVITY_HALF_LIFE_DAYS）：30 天沒登入 → 掉一半
      - 永不歸零（指數漸近 0）——曾建立的信任不該一刀切抹滅
      - 回流行為友善：回來後下次重算，inactive_days 歸零，分數立刻回升
    來源：學術界信任衰減共識（Temporal PageRank / time-decaying trust model）。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT last_active_at FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trust] activity collector failed for %s: %s", user_id, exc)
        return {"score": 0, "inactive_days": None, "last_active": None, "reason": f"error: {exc}"}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not row or row[0] is None:
        # 從未登入過（或 last_active_at 未記錄）→ 無活動訊號，不計分。
        return {"score": 0, "inactive_days": None, "last_active": None, "reason": "never_active"}

    last_active = row[0]
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=timezone.utc)
    inactive_days = max(0, (datetime.now(timezone.utc) - last_active).days)
    score = round(_MAX_ACTIVITY * math.exp(-_ACTIVITY_DECAY_K * inactive_days), 2)
    return {
        "score": score,
        "inactive_days": inactive_days,
        "last_active": last_active.isoformat(),
        "reason": "computed",
        "formula": "MAX_ACTIVITY × e^(−k·days), half_life=30d",
    }


async def recompute_user_trust(
    user_id: str,
    *,
    wallet_verified: Optional[bool] = None,
    wallet_address: Optional[str] = None,
    reason: str = "scheduled",
) -> Optional[Dict[str, Any]]:
    """重算單一使用者的 trust_score 並持久化。

    Args:
        user_id: 使用者 ID（evm_<地址>／tg_／g_；舊 TON 帳號即錢包地址）。
        wallet_verified: 覆寫「錢包所有權」判定；None＝依身份與綁定推：EVM 身份／
            綁定（user_wallets）或舊 TON 身份才算，Telegram／Google-only 帳號不算。
        wallet_address: 舊 TON 身份的地址（缺省時 user_id 是 TON 地址就用它）。
        reason: 重算原因（"scheduled" / "event_scam_hit" / "manual" / ...），進 audit。

    Returns:
        dict（含 score/tier/breakdown）或 None（失敗）。
    """
    if not user_id:
        return None
    ton_address = wallet_address or (user_id if is_ton_address(user_id) else None)

    # EVM 地址：user_wallets(chain='evm') → evm_ 身份 → users.evm_address。
    # 以前只讀 users.evm_address（旗標開著才會填），EVM 登入的人幾乎都沒有。
    evm_address = None
    try:
        from core.database.user import get_verified_evm_address

        evm_address = get_verified_evm_address(user_id)
    except Exception:  # noqa: BLE001
        pass

    if wallet_verified is None:
        wallet_verified = bool(ton_address or evm_address)

    # 並行採集所有 async 訊號（scam/activity 是 sync，直接呼叫）。
    onchain_task = collect_onchain_signals(ton_address) if ton_address else _noop_ton()
    passport_task = collect_passport_stamps(user_id)
    # 路線 D：EVM 訊號並行採集（無 EVM 地址或無服務 key → graceful 不計分）。
    # 產品鏈 Base 為主，主網為輔（兩條都有 30 天快取）。
    # 模式 B：傳 user_id 讓 collect_evm_onchain_signals 取使用者的 BYOK key
    if evm_address:
        evm_onchain_task = collect_evm_onchain_signals(
            evm_address, chain_id=platform_evm_chain_id(), user_id=user_id
        )
        evm_mainnet_task = collect_evm_onchain_signals(
            evm_address, chain_id=_ETHEREUM_MAINNET_CHAIN_ID, user_id=user_id
        )
    else:
        evm_onchain_task, evm_mainnet_task = _noop_evm(), _noop_evm()
    onchain, passport, evm_onchain, evm_mainnet = await asyncio.gather(
        onchain_task,
        passport_task,
        evm_onchain_task,
        evm_mainnet_task,
        return_exceptions=True,
    )
    if isinstance(onchain, BaseException):
        onchain = {"_error": str(onchain)}
    if isinstance(passport, BaseException):
        passport = {"stamps": {}, "_error": str(passport)}
    if isinstance(evm_onchain, BaseException):
        evm_onchain = {"_error": str(evm_onchain)}
    if isinstance(evm_mainnet, BaseException):
        evm_mainnet = {"_error": str(evm_mainnet)}
    # 詐騙扣分用真的鏈上地址查（以前拿 user_id 查，evm_0x… 永遠對不到），
    # 而且查帳號連結過的每一個地址——principal／鏈上訊號才只用主要地址
    scam_results = [
        collect_scam_penalty(a)
        for a in _penalty_addresses(user_id, ton_address, evm_address)
    ]
    scam = max(
        scam_results,
        key=lambda r: int(r.get("penalty", 0)),
        default={"penalty": 0, "reason": "no_address"},
    )
    activity = collect_activity_signals(user_id)

    # 路線 D：合併 TON + EVM 訊號——取最早 first_active（真實資歷）+ 合計 tx_count
    sources = [onchain, evm_onchain, evm_mainnet]
    candidates = [
        src.get("first_active")
        for src in sources
        if src.get("first_active") is not None
    ]
    earliest_first_active = min(candidates) if candidates else None

    tx_counts = [src.get("tx_count") or 0 for src in sources]
    combined_tx_count = sum(tx_counts) if any(tx_counts) else None

    # 餵給既有計算器。註：passport 不進 verified_stamps（那會給固定 20 分），
    # 改用漸進 bonus（score/100 × 20）在事後加——見下方。
    assessment: IdentityTrustAssessment = assess_identity_trust(
        wallet_verified=wallet_verified,
        wallet_first_active=earliest_first_active,
        wallet_tx_count=combined_tx_count,
        verified_stamps=None,
    )

    # Penalty（事後扣，floor 0）+ Activity（事後加）+ Passport 漸進（事後加）。
    # 三者對稱：都不進 assess_identity_trust（它只聚合「全有全無」的驗證訊號），
    # 而是在最終分數上調整——penalty 反映風險，activity 反映持續參與，
    # passport 反映漸進的 personhood 強度。
    penalty = int(scam.get("penalty", 0))
    activity_bonus = float(activity.get("score", 0))
    passport_bonus = float(passport.get("graduated_bonus", 0))
    final_score = min(
        100,
        max(0, assessment.trust_score - penalty + activity_bonus + passport_bonus),
    )
    tier = _tier_from_score(final_score)

    breakdown = {
        "base_assessment": assessment.to_audit_metadata(),
        "onchain": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in onchain.items()},
        "evm_onchain": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in (evm_onchain.items() if isinstance(evm_onchain, dict) else {})},
        "evm_mainnet_onchain": {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in evm_mainnet.items()
        },
        "merged_first_active": earliest_first_active.isoformat() if earliest_first_active else None,
        "merged_tx_count": combined_tx_count,
        "passport": passport,
        "passport_bonus_applied": passport_bonus,
        "activity": activity,
        "activity_bonus_applied": activity_bonus,
        "scam_penalty": scam,
        "penalty_applied": penalty,
        "final_score": final_score,
        "config": {
            "inactivity_decay_days": TRUST_INACTIVITY_DECAY_DAYS,
            "activity_half_life_days": _ACTIVITY_HALF_LIFE_DAYS,
            "activity_max": _MAX_ACTIVITY,
            "passport_threshold": HUMAN_PASSPORT_SCORER_THRESHOLD,
            "passport_configured": bool(HUMAN_PASSPORT_SCORER_ID and HUMAN_PASSPORT_API_KEY),
        },
    }

    _persist_trust_score(user_id, final_score, tier, breakdown, reason)
    return {"user_id": user_id, "trust_score": final_score, "tier": tier, "breakdown": breakdown}


def _tier_from_score(score: int) -> str:
    """分數 → Tier（對應 design doc 徽章設計 + assess_identity_trust 的 tier 語意）。

    兩套 tier 命名共存：
        - assess_identity_trust 的語意 tier（anonymous/known_wallet/soft_verified/strong_verified）
        - design doc 的徽章 tier（new/basic/member/regular/leader）
    本函式回傳「語意 tier」（與既有 consent_gate / chat-hitl 一致），徽章 tier
    在 API 層映射（別人看的 5 級徽章）。
    """
    if score >= 80:
        return "strong_verified"
    if score >= 60:
        return "soft_verified"
    if score >= 30:
        return "known_wallet"
    return "anonymous"


def _persist_trust_score(
    user_id: str,
    score: int,
    tier: str,
    breakdown: Dict[str, Any],
    reason: str,
) -> None:
    """寫入 user_trust_scores（歷史）+ 更新 users 快取欄位。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO user_trust_scores
                    (user_id, trust_score, tier, signal_breakdown, recompute_reason)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, score, tier, json.dumps(breakdown), reason),
            )
            c.execute(
                """
                UPDATE users
                SET trust_score = %s, trust_tier = %s,
                    trust_score_updated_at = NOW()
                WHERE user_id = %s
                """,
                (score, tier, user_id),
            )
        conn.commit()
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("[trust] persist failed for %s: %s", user_id, exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def get_latest_trust_score(user_id: str) -> Optional[Dict[str, Any]]:
    """讀最近一次算出的 trust_score（從 user_trust_scores 歷史表，含 breakdown）。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                """
                SELECT trust_score, tier, signal_breakdown, recompute_reason, created_at
                FROM user_trust_scores
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_id,),
            )
            row = c.fetchone()
            if not row:
                return None
            return {
                "trust_score": row[0],
                "tier": row[1],
                "breakdown": row[2],
                "recompute_reason": row[3],
                "computed_at": row[4].isoformat() if row[4] else None,
            }
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trust] get_latest failed for %s: %s", user_id, exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def get_user_trust_tier(user_id: str) -> str:
    """輕量讀 tier（從 users 快取欄位，徽章查詢用，免 join）。"""
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute("SELECT trust_tier FROM users WHERE user_id = %s", (user_id,))
            row = c.fetchone()
            return row[0] if row else "anonymous"
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("[trust] get_tier failed for %s: %s", user_id, exc)
        return "anonymous"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "collect_onchain_signals",
    "collect_scam_penalty",
    "collect_passport_stamps",
    "collect_activity_signals",
    "recompute_user_trust",
    "get_latest_trust_score",
    "get_user_trust_tier",
]
