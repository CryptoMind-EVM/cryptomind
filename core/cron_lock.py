"""cron 單一實例鎖：上一輪還沒跑完時，這一輪直接跳過。

cron 每次都起新 process，腳本內的 asyncio.Semaphore 管不到跨 process；
跑得比排程間隔久（錢包監測 10 分鐘、早報 1 小時）就會兩份同時跑、重複發通知。
所有 cron 都在同一個容器裡，用 /tmp 的檔案鎖就夠（process 結束鎖自動釋放）。
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from typing import IO, Optional


def acquire(name: str) -> Optional[IO]:
    """拿到鎖回傳檔案物件（呼叫端要一直持有到結束）；已有人在跑回 None。"""
    path = os.path.join(tempfile.gettempdir(), f"cron-{name}.lock")
    fh = open(path, "w")  # noqa: SIM115 — 鎖要活到 process 結束
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh
