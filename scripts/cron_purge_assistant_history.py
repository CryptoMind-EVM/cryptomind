"""定時任務：清掉過期的聊天室 AI 助理問答（每天一次）。

讀取時本來就只給保留天數內的（chat_assistant_history_repo.RETENTION）；這裡把超過的真的刪掉，
不然很久沒開聊天室的人那幾列會一直留著。冪等，失敗只記 log（下次再清）。
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [purge-assistant-history] %(message)s"
)
from config.logging_config import install_secret_redaction  # noqa: E402

install_secret_redaction()
logger = logging.getLogger(__name__)


async def run() -> int:
    from core.orm.chat_assistant_history_repo import chat_assistant_history_repo

    purged = await chat_assistant_history_repo.purge_expired()
    logger.info("purged=%d", purged)
    return purged


def main() -> int:
    try:
        asyncio.run(run())
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("purge crashed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
