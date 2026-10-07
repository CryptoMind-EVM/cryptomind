"""平台免費模型的結果全站共用（2026-09-28 DANNY：免費模型的結果盡量共用、減少平台消耗）。

輸入只有公開資料（行情、新聞、公開專案）的 AI 結果，用平台模型（local_llama／keyless）
產生時每個人拿到的本來就一樣——快取擁有者改成 ``PLATFORM_OWNER``，第一個人觸發、
其他人直接拿現成的。自帶金鑰的結果仍各自一份（花的是他的錢，模型也不同）。

對話、早報那一句這類帶個人資料的輸出不走這裡。
"""

from __future__ import annotations

import asyncio

# 快取的「使用者」欄位：真實 user_id 不會是這個值
PLATFORM_OWNER = "_platform"

# 同一個 process 內同一份結果同時被多人觸發時只算一次（其他人等第一個算完直接拿快取）
_locks: dict[str, asyncio.Lock] = {}


def is_platform_model(provider: str | None) -> bool:
    """平台自架、不花使用者錢的模型。"""
    from core.model_config import is_keyless_provider, is_local_provider

    return is_local_provider(provider) or is_keyless_provider(provider or "")


def cache_owner(provider: str | None, user_id: str) -> str:
    return PLATFORM_OWNER if is_platform_model(provider) else user_id


def lock_for(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())
