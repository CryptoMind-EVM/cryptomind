"""定時任務：系統事件同步（每天一次，早報前跑）。

對每個早報候選人（綁 Telegram 或設過偏好）依持倉產生：美股財報日、台股月營收
與季報截止、代幣解鎖（DefiLlama 索引）、總經（FOMC／CPI／非農，偏好 include_macro）。
解鎖索引與總經清單一批人只抓一次。事件用 dedupe_key 去重，重跑不會重複。
單人失敗不中斷批次。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [calendar-sync] %(message)s"
)
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


def run(only_user: str | None = None) -> dict:
    from core.daily_brief import store
    from core.daily_brief.calendar_sync import (
        default_macro_events,
        default_unlock_index,
        sync_user_events,
    )
    from core.daily_brief.schedule import effective_prefs

    rows = [store.get_prefs_row(only_user)] if only_user else store.list_candidates()
    rows = [r for r in rows if r]
    summary = {"users": len(rows), "events": 0, "failed": 0}
    now_utc = datetime.now(timezone.utc)
    # 共用資料源：一批人抓一次（解鎖索引 22 MB、總經抓官網）
    unlock_index = default_unlock_index() if rows else {}
    macro_by_day: dict = {}
    for row in rows:
        try:
            prefs = effective_prefs(row)
            try:
                tz = ZoneInfo(prefs.timezone)
            except Exception:  # noqa: BLE001
                tz = ZoneInfo("UTC")
            today = now_utc.astimezone(tz).date()
            if today not in macro_by_day:
                macro_by_day[today] = default_macro_events(today)
            summary["events"] += sync_user_events(
                prefs.user_id,
                today,
                prefs.language,
                include_macro=prefs.include_macro,
                unlock_index=unlock_index,
                macro_events=macro_by_day[today],
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — 單人失敗不中斷批次
            logger.warning("user=%s failed: %s", row.get("user_id"), exc)
            summary["failed"] += 1
    logger.info(
        "users=%d events=%d failed=%d",
        summary["users"],
        summary["events"],
        summary["failed"],
    )
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="系統事件同步（每日）")
    ap.add_argument("--user", help="只處理這個 user_id")
    args = ap.parse_args(argv)
    try:
        run(only_user=args.user)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("calendar sync crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
