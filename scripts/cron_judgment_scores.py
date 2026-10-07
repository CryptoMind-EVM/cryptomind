"""定時任務：判斷評分（每天一次，鏈上同步之後）。

1. 對近期有交易的使用者建 pending 列（冪等）；2. 對到期的列抓收盤價評分。
單人／單列失敗不中斷批次。
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [judgment-scores] %(message)s"
)
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


def run(only_user: str | None = None) -> dict:
    from core.scorecard import service, store

    users = [only_user] if only_user else store.users_with_trades()
    summary = {"users": len(users), "created": 0, "failed_users": 0}
    for user_id in users:
        try:
            summary["created"] += service.build_pending_for_user(user_id)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("user=%s build failed: %s", user_id, exc)
            summary["failed_users"] += 1
    today = datetime.now(timezone.utc).date()
    summary.update(service.score_due(today))
    logger.info(
        "users=%d created=%d scored=%d pending=%d unscorable=%d errors=%d failed_users=%d",
        summary["users"],
        summary["created"],
        summary["scored"],
        summary["pending"],
        summary["unscorable"],
        summary["errors"],
        summary["failed_users"],
    )
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判斷評分（每日）")
    ap.add_argument("--user", help="只處理這個 user_id")
    args = ap.parse_args(argv)
    try:
        run(only_user=args.user)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("judgment scores crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
