"""
延遲載入重型第三方模組（第一次存取屬性時才真的 import）。

用法（取代 ``import yfinance as yf``）：

    from utils.lazy_import import lazy_module
    yf = lazy_module("yfinance")

之後 ``yf.Ticker(...)`` 照常用，只是第一次呼叫才會付 import 成本。
模組一旦載入就跟一般 import 完全相同（同一個 sys.modules 物件）。

為什麼：API 進程啟動時把 yfinance（連帶 curl_cffi、bs4）、pandas_ta（連帶
numba/llvmlite）全部載進來，佔 ~90 MB 常駐記憶體，但 /health、登入、論壇這些
路徑根本用不到。改成延遲載入後只有真的算指標 / 抓行情的請求才付這個成本。

限制：只適用「模組整個取別名」的寫法（``yf.xxx``）；``from x import y`` 這種
拿到的是屬性，會立刻觸發載入，該改成函式內 import。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from types import ModuleType


def lazy_module(name: str) -> ModuleType:
    """回傳 ``name`` 的延遲載入模組物件；已載入過就直接回傳既有模組。"""
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        # 找不到就走一般 import，讓錯誤在這裡直接爆出來，不要延後到第一次使用
        return importlib.import_module(name)
    loader = importlib.util.LazyLoader(spec.loader)
    spec.loader = loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module
