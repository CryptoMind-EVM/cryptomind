"""
群組 @提及：訊息原文照存「@暱稱」（預覽、通知、AI 助理、檢舉快照都直接看得懂），
送出與顯示時拿當下的成員名單比對出被提及的人。

- 每個 @ 從後面接的字比成員名字，最長的優先（「@Danny」不會同時算成 Dan）；不分大小寫。
- 全形「＠」也算：注音輸入法預設打出全形。
- 名字後面不要求空白：中文常寫「@小明你看」。
- 撞名就全部算進去（選單選的是誰後端分不出來，多通知比漏掉好）。
- 限制：改名後舊訊息不再標示；後來有人改成同名，舊訊息會標到他（只影響顯示，通知在送出當下就發了）。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# 半形、全形（注音預設全形）；前端 dm-message-actions.js / group-mention-picker.js 同
AT_SIGNS = re.compile("[@＠]")


def find_mentions(text: str, names: Dict[str, Optional[str]]) -> List[dict]:
    """[{user_id, name}]，依第一次出現的順序、同一人只列一次"""
    by_name: Dict[str, List[tuple]] = {}
    for uid, name in names.items():
        if name:
            by_name.setdefault(name.lower(), []).append((uid, name))
    if not text or not by_name:
        return []
    candidates = sorted(by_name, key=len, reverse=True)
    result, seen = [], set()
    pos = 0
    while (at := AT_SIGNS.search(text, pos)) is not None:
        start = at.end()
        hit = next(
            (c for c in candidates if text[start : start + len(c)].lower() == c),
            None,
        )
        if hit is None:
            pos = start
            continue
        for uid, name in by_name[hit]:
            if uid not in seen:
                seen.add(uid)
                result.append({"user_id": uid, "name": name})
        pos = start + len(hit)
    return result
