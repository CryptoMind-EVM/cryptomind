"""回覆快取（L5）——只快取「沒有呼叫任何工具」的回覆。

為什麼不是一般意義的 semantic cache
-----------------------------------
原始構想是「高頻問題語意相似就命中」。在金融場景直接這樣做有兩個真實風險，
所以這裡刻意收斂：

1. **陳舊資料**。「BTC 現在多少錢」快取 60 秒，回的就是 60 秒前的價格，而使用者
   看不出來。在投資情境給過期報價是信任問題，不是效能問題。
   → 解法：**只快取 tool_calls == 0 的回覆**。沒呼叫工具就代表答案不是即時
   市場資料，而是解釋性/一般性內容（「什麼是 RSI」「你能做什麼」），
   本來就不會過期。這條規則讓陳舊風險從設計上消失，而不是靠調 TTL 去賭。

2. **跨使用者汙染 + 個人化外洩**。回覆裡可能帶使用者暱稱、記憶脈絡、錢包上下文。
   而且模糊比對會把「BTC 怎麼樣」的答案配給「ETH 怎麼樣」——在金融場景是
   嚴重錯誤。
   → 解法：key 綁 user_id，且用 normalize 後的**完整比對**而非向量相似度。

跨使用者共享快取（真正的 semantic cache）要先解決個人化剝離與相似度閾值，
是獨立的產品決策，不在此模組範圍。

不引入新依賴：純 stdlib，程序內 LRU + TTL，服務重啟自動清空。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional

from core.agents.triage import normalize_query

# 解釋性內容不會過期，但仍給一個上限，避免回覆長期偏離改版後的產品說明。
TTL_SECONDS = 300
MAX_ENTRIES = 2000

# (user_id, normalized_query, language) → {"response": str, "ts": float}
_cache: "OrderedDict[tuple, dict]" = OrderedDict()


def _key(user_id: Optional[str], query: str, language: str) -> tuple:
    return (user_id or "anonymous", normalize_query(query), language or "")


def _sweep(now: float) -> None:
    stale = [k for k, v in _cache.items() if (now - v["ts"]) >= TTL_SECONDS]
    for k in stale:
        _cache.pop(k, None)


def get(user_id: Optional[str], query: str, language: str) -> Optional[str]:
    """取回快取的回覆；未命中或已過期回 None。"""
    if not query or not query.strip():
        return None
    now = time.time()
    entry = _cache.get(_key(user_id, query, language))
    if not entry:
        return None
    if (now - entry["ts"]) >= TTL_SECONDS:
        _cache.pop(_key(user_id, query, language), None)
        return None
    _cache.move_to_end(_key(user_id, query, language))
    return entry["response"]


def put(
    user_id: Optional[str],
    query: str,
    language: str,
    response: str,
    *,
    tool_calls: int,
) -> bool:
    """存入快取。回傳是否真的存了。

    ``tool_calls > 0`` 一律不存——那代表回覆含即時市場資料，快取會給出過期報價。
    這是本模組最重要的一條規則，見 module docstring。
    """
    if tool_calls > 0:
        return False
    if not query or not query.strip() or not response or not response.strip():
        return False

    now = time.time()
    _sweep(now)
    while len(_cache) >= MAX_ENTRIES:
        _cache.popitem(last=False)
    _cache[_key(user_id, query, language)] = {"response": response, "ts": now}
    return True


def invalidate_user(user_id: str) -> int:
    """清掉某使用者的所有快取（改暱稱、改設定後呼叫）。回傳清掉幾筆。"""
    stale = [k for k in _cache if k[0] == user_id]
    for k in stale:
        _cache.pop(k, None)
    return len(stale)


def clear() -> None:
    """清空（測試用）。"""
    _cache.clear()
