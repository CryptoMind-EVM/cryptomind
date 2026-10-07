"""行情路由共用的程序內 TTL 快取。

原本 commodity／forex／astock／hkstock／instock／jpstock／krstock／usstock 八個路由
各自抄一份一模一樣的 `_get_cache`／`_set_cache`（只差預設 TTL），而且沒有上限——
使用者查過的每個代號都永遠留在記憶體裡。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class TTLCache:
    """dict 快取；超過 ``max_entries`` 時先清過期的，還是太多就丟最早放進來的。"""

    def __init__(self, default_ttl: int = 300, max_entries: int = 2000):
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._data: Dict[str, Tuple[Any, datetime]] = {}

    def get(self, key: str) -> Any:
        hit = self._data.get(key)
        if hit and datetime.now(timezone.utc) < hit[1]:
            return hit[0]
        return None

    def set(self, key: str, data: Any, ttl: Optional[int] = None) -> None:
        now = datetime.now(timezone.utc)
        seconds = self.default_ttl if ttl is None else ttl
        self._data[key] = (data, now + timedelta(seconds=seconds))
        if len(self._data) > self.max_entries:
            for k in [k for k, (_, exp) in self._data.items() if exp <= now]:
                del self._data[k]
            while len(self._data) > self.max_entries:
                del self._data[next(iter(self._data))]

    def entry(self, key: str) -> Optional[Tuple[Any, datetime]]:
        """(data, expiry)，過期的也回（給 swr 判斷能不能先拿舊的頂著）。"""
        return self._data.get(key)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


# ── stale-while-revalidate ────────────────────────────────────────────────────
# 行情快取 TTL 只有 60 秒～5 分鐘，過期後下一個人要同步等外部 API 0.8–2 秒
#（2026-09-26 正式站量測；流量低時幾乎每個人都碰到）。過期但還不算太舊時先回舊資料、
# 背景重算；同一個 key 同時只有一個重算在跑。太舊（超過 max_stale）或沒有資料才同步算。
_refreshing: set = set()
_background: set = set()


def schedule_refresh(tag: Tuple[int, str], refresh: Callable[[], Awaitable[None]]) -> None:
    """背景重算；同一個 tag 已經在跑就不重複排。例外只記 log，舊資料繼續用。"""
    if tag in _refreshing:
        return
    _refreshing.add(tag)

    async def _run():
        try:
            await refresh()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # 背景更新失敗：保留舊資料，下一次再試
            logger.warning("[swr] background refresh %s failed: %s", tag[1], e)
        finally:
            _refreshing.discard(tag)

    task = asyncio.get_running_loop().create_task(_run())
    _background.add(task)  # 保留引用，免得 task 被 GC 掉
    task.add_done_callback(_background.discard)


async def swr(
    cache: TTLCache,
    key: str,
    compute: Callable[[], Awaitable[Any]],
    ttl: Optional[int] = None,
    max_stale: int = 600,
) -> Any:
    """快取有效 → 直接回；過期未滿 max_stale 秒 → 回舊的並背景重算；否則同步算並寫入。"""
    hit = cache.entry(key)
    if hit is not None:
        data, expiry = hit
        now = datetime.now(timezone.utc)
        if now < expiry:
            return data
        if now - expiry < timedelta(seconds=max_stale):

            async def _refresh():
                cache.set(key, await compute(), ttl)

            schedule_refresh((id(cache), key), _refresh)
            return data
    data = await compute()
    cache.set(key, data, ttl)
    return data
