"""Skill 使用量指標（2026-09-12）。

skill 目錄靜態、沒有任何命中資料——不知道哪些 skill 從沒被載入、哪些常被
載入，就沒法決定要不要精簡目錄或開放使用者自建。做法跟 RunMetrics 一樣：
不引入 metrics backend，先落結構化 log。

- ``record_skill_load(name)``：load_skill 成功時呼叫（contextvar 收集本次 run
  載入的 skill；跨 async task 由 contextvar 隔離）。
- ``drain_loaded_skills()``：RunMetrics 行尾用（``skills=a,b``），並清空。
- 程序內累計計數 ``snapshot()``：admin 統計／除錯用，服務重啟歸零。
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_loaded: ContextVar[Optional[List[str]]] = ContextVar("loaded_skills", default=None)
_counts: Dict[str, int] = {}
_lock = threading.Lock()


def reset_loaded_skills() -> None:
    """每次 run 開頭呼叫：清掉上一題殘留（manager 跨請求快取）。"""
    _loaded.set([])


def record_skill_load(name: str) -> None:
    if not name:
        return
    bucket = _loaded.get()
    if bucket is None:
        bucket = []
        _loaded.set(bucket)
    if name not in bucket:
        bucket.append(name)
    with _lock:
        _counts[name] = _counts.get(name, 0) + 1
    logger.info("[SkillMetrics] loaded skill=%s", name)


def drain_loaded_skills() -> List[str]:
    bucket = _loaded.get() or []
    _loaded.set([])
    return list(bucket)


def snapshot() -> Dict[str, int]:
    with _lock:
        return dict(_counts)
