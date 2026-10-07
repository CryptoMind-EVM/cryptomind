"""定時任務：綁定錢包鏈上轉帳 → 帳本（每天一次，行事曆同步之前）。

對每個「有綁定錢包（或 TON 身份）且沒關掉同步」的人跑 ``core.onchain.sync.sync_user``；
唯一索引讓重跑冪等。單人失敗不中斷批次。
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [onchain-sync] %(message)s")
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


def run(only_user: str | None = None) -> dict:
    from core.onchain import store
    from core.onchain.sync import sync_user

    users = [only_user] if only_user else store.list_sync_candidates()
    summary = {"users": len(users), "added": 0, "failed": 0}
    for user_id in users:
        try:
            result = sync_user(user_id)
            summary["added"] += int(result.get("added") or 0)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # noqa: BLE001 — 單人失敗不中斷批次
            logger.warning("user=%s failed: %s", user_id, exc)
            summary["failed"] += 1
    logger.info(
        "users=%d added=%d failed=%d",
        summary["users"],
        summary["added"],
        summary["failed"],
    )
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="綁定錢包鏈上轉帳同步（每日）")
    ap.add_argument("--user", help="只處理這個 user_id")
    args = ap.parse_args(argv)
    try:
        run(only_user=args.user)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("onchain sync crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
