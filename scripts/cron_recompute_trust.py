"""
定時任務：重算所有使用者的 trust_score

每天 UTC 00:30（接在 daily board 後）執行。對每個有錢包的使用者（EVM 登入身份、
綁了 EVM 錢包、或舊 TON 登入身份）：
    1. 重新採集鏈上訊號（年齡、活動度）
    2. 重算 assess_identity_trust
    3. 套用詐騙 DB penalty
    4. 持久化到 user_trust_scores + users 快取

衰退機制（學 Discourse TL3 + 學術界指數衰減共識）內建於此 cron：
    - 鏈上 last_activity 會自然變舊 → onchain 訊號分數隨時間反映
    - 平台活動度：collect_activity_signals 讀 users.last_active_at，每次重算時
      依半衰期 30 天指數衰減（T(t)=T₀·e^(−kt)）。回來登入 → 下次重算分數回升。

設計：
    - 單一失敗不中斷批次（per-user try/except）。
    - 回傳統計 dict 供 log 觀測。
    - graceful：DB 無使用者 / 無錢包使用者都安全 skip。
"""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import TRUST_SCORE_ENABLED
from core.database.connection import get_connection
from core.identity.scoring import recompute_user_trust
from core.onchain.addresses import is_ton_address

logging.basicConfig(level=logging.INFO, format="%(asctime)s [trust-cron] %(message)s")
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


# 有已驗證 EVM 地址（同 core.database.user.get_verified_evm_address 的三個來源）
_HAS_EVM_SQL = """(
    left(u.user_id, 4) = 'evm_'
    OR u.evm_address IS NOT NULL
    OR EXISTS (SELECT 1 FROM user_wallets w WHERE w.user_id = u.user_id AND w.chain = 'evm')
)"""


def _fetch_wallet_users() -> list[tuple[str, bool]]:
    """讀所有有錢包的使用者 → [(user_id, has_evm)]。

    - EVM：evm_ 登入身份、user_wallets 綁定、users.evm_address（2026-09-25 前只算 TON，
      登入 2026-08-31 起統一 EVM 後等於幾乎沒人被重算）。
    - 舊 TON 身份：原本用 ``user_id LIKE 'EQ%'`` 認錢包——UQ、testnet、raw 寫法的人全被
      跳過。SQL 先粗篩地址形狀（friendly 48 字元／raw），再用 is_ton_address 解碼確認。
    """
    conn = get_connection()
    try:
        with conn.cursor() as c:
            c.execute(
                f"""
                SELECT u.user_id, {_HAS_EVM_SQL} AS has_evm
                FROM users u
                WHERE u.is_active = TRUE
                  AND ({_HAS_EVM_SQL}
                       OR length(u.user_id) = 48
                       OR u.user_id ~ '^-?[0-9]+:[0-9a-fA-F]{{64}}$')
                """
            )
            return [
                (uid, bool(has_evm))
                for uid, has_evm in c.fetchall()
                if has_evm or is_ton_address(uid)
            ]
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


async def _recompute_one(user_id: str) -> tuple[str, bool, int | None, str | None]:
    """重算單一使用者。回 (user_id, success, score, tier_or_error)。"""
    try:
        result = await recompute_user_trust(user_id, reason="scheduled")
        if result is None:
            return (user_id, False, None, "recompute returned None")
        return (user_id, True, result["trust_score"], result["tier"])
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — per-user 隔離
        return (user_id, False, None, str(exc))


async def run_job() -> dict:
    """主任務。回統計 dict。"""
    if not TRUST_SCORE_ENABLED:
        logger.info("TRUST_SCORE_ENABLED=false, skip")
        return {"success": True, "skipped": True, "reason": "feature_disabled"}

    users = _fetch_wallet_users()
    if not users:
        logger.info("沒有錢包使用者需要重算")
        return {"success": True, "processed": 0, "succeeded": 0, "failed": 0}

    logger.info("開始重算 %d 位使用者的 trust_score", len(users))
    # 並行但限流——避免同時打爆 TonAPI／Etherscan（保守 sequential-ish，小 batch）。
    sem = asyncio.Semaphore(5)

    async def _bounded(uid: str) -> tuple[str, bool, int | None, str | None]:
        async with sem:
            return await _recompute_one(uid)

    results = await asyncio.gather(*[_bounded(uid) for uid, _ in users])

    succeeded = sum(1 for _, ok, _, _ in results if ok)
    failed = len(results) - succeeded
    logger.info("重算完成：成功 %d / 失敗 %d / 總計 %d", succeeded, failed, len(results))
    for uid, ok, _score, err in results:
        if not ok:
            logger.warning("  失敗 user=%s: %s", uid, err)
    return {"success": True, "processed": len(results), "succeeded": succeeded, "failed": failed}


def main() -> int:
    """Entry point for cron / direct invocation."""
    try:
        stats = asyncio.run(run_job())
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("trust cron 嚴重失敗: %s", exc)
        return 1
    logger.info("stats: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
