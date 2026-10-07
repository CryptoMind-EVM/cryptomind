#!/usr/bin/env python
"""一次性 ops 腳本：開啟 wallet-monitor entitlement gate 前的通知（policy B）。

design 2026-08-13 §9.7 決策 #11 = B（通知後停止）：在把
``WALLET_MONITOR_PREMIUM_GATE_ENABLED`` 打開之前，先對「已有監測設定但非 active
premium」的使用者發站內通知，告知持續監測即將改為 Premium 功能、升級可續用，
避免他們突然發現沒有警示。

行為：
- 預設 **dry-run**（只列出會被通知的對象，不寫入）。
- ``--apply`` 才真正發送通知 + 在 wallet_alert_settings 標記
  ``monitor_migration_notified=true``（防重複通知）。
- 已標記者跳過（冪等，可重複執行）；active premium 使用者跳過（不受影響）。

用法::

    .venv/bin/python scripts/notify_wallet_monitor_migration.py            # dry-run
    .venv/bin/python scripts/notify_wallet_monitor_migration.py --apply    # 真正發送
    .venv/bin/python scripts/notify_wallet_monitor_migration.py --apply --limit 5

> 這是營運腳本（會讀使用者資料、寫通知），執行前應由 DANNY 確認。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# 一次性 ops 腳本，由 DANNY 手動執行——自行把專案根加入 sys.path，免依賴部署 PYTHONPATH。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database.connection import get_connection
from core.database.notifications import create_notification
from core.database.user import (
    get_user_membership,
    save_wallet_alert_settings,
)
from core.entitlement import Entitlement, resolve_entitlement

logging.basicConfig(level=logging.INFO, format="%(asctime)s [migration-notify] %(message)s")
logger = logging.getLogger(__name__)

NOTIFY_TYPE = "system_update"
NOTIFY_TITLE = "Wallet monitoring is becoming a Premium feature"
NOTIFY_BODY = (
    "Continuous wallet monitoring and proactive alerts are moving to Premium. "
    "Upgrade before it takes effect to keep your monitoring running — "
    "your current wallets and settings are preserved either way."
)


def _should_notify(settings: dict, ent: Entitlement) -> tuple[bool, str]:
    """回傳 (是否要通知, 原因)。純函式，供單元測試。"""
    if ent.is_premium:
        return False, "premium_unaffected"
    if isinstance(settings, dict) and settings.get("monitor_migration_notified"):
        return False, "already_notified"
    return True, "eligible"


def _fetch_users_with_monitors() -> list[tuple[str, dict]]:
    """讀所有活躍且有 monitored_wallets 設定的使用者（與 cron 同條件）。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, wallet_alert_settings
                FROM users
                WHERE wallet_alert_settings IS NOT NULL
                  AND is_active = TRUE
                """
            )
            rows = cur.fetchall()
        out: list[tuple[str, dict]] = []
        for user_id, raw in rows:
            try:
                s = raw if isinstance(raw, dict) else json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(s, dict) and s.get("monitored_wallets"):
                out.append((user_id, s))
        return out
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _mask(uid: str) -> str:
    """log 用，遮蔽 user_id（TON 地址）中段。"""
    if not uid or len(uid) < 10:
        return "***"
    return uid[:4] + "..." + uid[-4:]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Wallet-monitor gate 遷移通知（policy B）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true", help="實際發送通知（預設 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="最多處理 N 位（測試用，0=不限）")
    args = ap.parse_args()

    users = _fetch_users_with_monitors()
    logger.info("有監測設定的活躍使用者：%d 位", len(users))

    notified = 0
    skipped_premium = 0
    skipped_already = 0
    failed = 0

    for user_id, settings in users:
        try:
            ent = resolve_entitlement(membership=get_user_membership(user_id))
        except Exception as e:  # noqa: BLE001
            # membership 查不到時 resolve_entitlement 內部已 fail-safe 成 free；
            # 此處防禦性記錄後保守地不通知（避免誤發）。
            logger.warning("  %s membership 查詢例外，跳過：%s", _mask(user_id), e)
            failed += 1
            continue

        do_notify, reason = _should_notify(settings, ent)
        if reason == "premium_unaffected":
            skipped_premium += 1
            continue
        if reason == "already_notified":
            skipped_already += 1
            continue
        if not do_notify:
            continue

        if args.limit and notified >= args.limit:
            logger.info("達到 --limit %d，停止。", args.limit)
            break

        if not args.apply:
            logger.info("  [dry-run] 將通知 %s（tier=%s）", _mask(user_id), ent.tier)
            notified += 1
            continue

        try:
            create_notification(
                user_id, NOTIFY_TYPE, NOTIFY_TITLE, NOTIFY_BODY,
                {"migration": "wallet_monitor"},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("  %s 通知發送失敗：%s", _mask(user_id), e)
            failed += 1
            continue

        # 標記已通知（冪等）。失敗只 log，不阻擋——最壞情況是重複通知，可接受。
        settings["monitor_migration_notified"] = True
        try:
            save_wallet_alert_settings(user_id, settings)
        except Exception as e:  # noqa: BLE001
            logger.warning("  %s 標記失敗（非致命，可能重複通知）：%s", _mask(user_id), e)

        notified += 1
        logger.info("  已通知 %s", _mask(user_id))

    logger.info(
        "%s 完成：通知=%d / 跳過_premium=%d / 跳過_已通知=%d / 失敗=%d",
        "APPLY" if args.apply else "DRY-RUN",
        notified, skipped_premium, skipped_already, failed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
