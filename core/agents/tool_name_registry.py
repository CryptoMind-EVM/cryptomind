"""
動態 tool name 註冊表 — 解決 claw_loop._strip_tool_name_leaks 硬編碼清單問題。

背景
----
``claw_loop._strip_tool_name_leaks`` 原本 hardcode 一份 20 個 tool name 清單，
新增 tool 時必須手動同步，是維護地雷（commit 103fad7 引入 sanitizer 時的 trade-off）。
原註解承認「不從 registry 動態抓是為避免循環依賴」——但 ``tool_registry.py``
本身沒 import 任何 agent，真正風險是 bootstrap ↔ claw_loop 之間。

設計
----
- ``bootstrap()`` 跑過後把 ``tool_registry._tools.keys()`` 注入 ``_REGISTERED_NAMES``
- ``claw_loop._strip_tool_name_leaks`` 改用 ``get_registered_tool_names()`` 動態讀
- ``_FALLBACK_NAMES`` 保留：單元測試直接 import claw_loop（bootstrap 沒跑）時用

啟用前後差異：
- 單元測試 / 純 import 場景：用 fallback 清單（功能不變）
- 正式呼叫 / scenario 執行：用動態清單（新增 tool 自動被 sanitizer 覆蓋）

效能（G6 修復）
--------------
舊版 sanitizer 在每次回應都重建 ~240 個 regex（base + _tool 變體），跑數百次
re.sub。改良：把所有 tool name 編進「單一 alternation regex」（module-level cache），
``register_tool_names()`` 時 invalidate cache。
"""
from __future__ import annotations

import re
from typing import Set

# bootstrap() 結尾呼叫 register_tool_names() 注入；預設空集合。
_REGISTERED_NAMES: Set[str] = set()

# Fallback 清單：bootstrap 未跑時（單元測試直接 import claw_loop）用。
# 與 commit 103fad7 原本 hardcode 的清單一致，確保行為不退化。
_FALLBACK_NAMES: Set[str] = {
    "get_crypto_price",
    "resolve_symbol",
    "us_stock_price",
    "tw_stock_price",
    "technical_analysis",
    "us_technical_analysis",
    "tw_technical_analysis",
    "aggregate_news",
    "google_news",
    "us_news",
    "get_commodity_price",
    "get_forex_rate",
    "web_search",
    "get_crypto_market_cap",
    "get_fear_and_greed_index",
    "check_token_security",
    "check_address_safety",
    "assess_jetton_safety",
    "load_skill",
    "load_knowledge",
    # 常見的 LangChain @tool 自動加 _tool 後綴
    "get_crypto_price_tool",
    "web_search_tool",
    "technical_analysis_tool",
    # 第二梯隊（commodity / forex / global stock / economic）— fallback 用
    "get_commodity_futures_price",
    "get_all_commodities_prices",
    "get_gold_silver_ratio",
    "get_oil_price_analysis",
    "get_all_forex_rates",
    "get_usd_twd_rate",
    "get_central_bank_rates",
    "global_stock_price",
    "global_stock_technical",
    "global_stock_fundamentals",
    "global_stock_news",
    "global_stock_snapshot",
    "get_market_indices",
    "get_vix_index",
    "get_sp500_performance",
    "get_sector_performance",
    "get_economic_calendar",
    "tw_news",
    "tw_major_news",
    "tw_pe_ratio",
    "tw_monthly_revenue",
    "tw_dividend",
    "tw_foreign_top20",
    "tw_stock_snapshot",
    "tw_market_index",
    "us_fundamentals",
    "us_earnings",
    "us_institutional_holders",
    "us_insider_transactions",
    "us_stock_snapshot",
    "get_gas_fees",
    "get_whale_alerts",
    "get_exchange_flow",
    "get_staking_yield",
    "get_dex_volume",
    "get_dex_pair_info",
    "get_trending_dex_pairs",
    "get_eth_balance",
    "get_erc20_token_balance",
    "get_address_transactions",
    "get_contract_info",
    "get_eth_price_etherscan",
    "get_cmc_quote",
    "get_defillama_tvl",
    "get_crypto_categories_and_gainers",
    "get_token_unlocks",
    "get_token_supply",
    "get_futures_data",
    "get_current_time_taipei",
    "tool_result_retrieve",
    "request_more_tools",  # 工具櫥窗的出口工具（不在 registry，見 core/agents/tool_showcase.py）
    "read_more_result",  # 工具輸出分頁續讀（不在 registry，見 core/agents/tool_compactor.py）
}


def register_tool_names(names: Set[str]) -> None:
    """bootstrap() 結尾呼叫，把 registry 內所有 tool name 注入。

    Args:
        names: ``tool_registry._tools.keys()`` 轉成的 set。
    """
    global _REGISTERED_NAMES, _COMPILED_PATTERNS_CACHE, _COMPILED_BARE_NAME_CACHE
    _REGISTERED_NAMES = set(names)
    # G6: invalidate 編譯過的 regex cache，下次 _get_compiled_sanitizer_patterns
    # / _get_compiled_bare_name_pattern 會用新清單重建。
    _COMPILED_PATTERNS_CACHE = None
    _COMPILED_BARE_NAME_CACHE = None


def get_registered_tool_names() -> Set[str]:
    """取得目前已知 tool names。

    Returns:
        若 bootstrap 已跑（_REGISTERED_NAMES 非空）用動態清單；
        否則用 _FALLBACK_NAMES（單元測試場景）。
    """
    return _REGISTERED_NAMES if _REGISTERED_NAMES else _FALLBACK_NAMES


# ============================================================================
# Display-name lookup — 把內部 tool_id 對應到人類可讀名稱。
#
# 解決問題：claw_loop 的 SSE progress 事件（_on_tool_start / _on_tool_end）
# 原本把原始 tool_id（如 get_crypto_price）直接塞進 i18n 模板
# `progress.querying_tool`（"正在查詢：{name}"）→ 洩漏內部架構給客戶端。
# 改用本函式把 tool_id 對應到 _TOOLS_SEED 的 display_name（如 "即時加密貨幣價格"）。
#
# 來源：core/database/tools.py 的 _TOOLS_SEED（與前端 tools catalog 同一份）。
# core/agents → core/database 是既有依賴方向（base_react_agent.py 已這樣做），
# 不會造成循環依賴。
# ============================================================================
_TOOL_DISPLAY_NAMES_CACHE: dict[str, str] | None = None


def _load_tool_display_names() -> dict[str, str]:
    """從 _TOOLS_SEED 建構 tool_id → display_name map（module-level cache）。"""
    global _TOOL_DISPLAY_NAMES_CACHE
    if _TOOL_DISPLAY_NAMES_CACHE is not None:
        return _TOOL_DISPLAY_NAMES_CACHE
    result: dict[str, str] = {}
    try:
        from core.database.tools import _TOOLS_SEED  # type: ignore[attr-defined]

        for entry in _TOOLS_SEED:
            tool_id = entry.get("tool_id")
            display = entry.get("display_name")
            if tool_id and display:
                result[tool_id] = display
    except Exception:  # noqa: BLE001
        # 測試環境可能沒有 _TOOLS_SEED；回空 map，get_tool_display_name 會 fallback
        pass
    _TOOL_DISPLAY_NAMES_CACHE = result
    return result


# fallback 詞（tool_id 找不到對應時的中性詞），4 語。
# 不回傳原 tool_id，避免洩漏內部架構。
_FALLBACK_TOOL_NAME = {
    "zh-TW": "工具",
    "zh-CN": "工具",
    "en": "tool",
    "ru": "инструмент",
}


def get_tool_display_name(tool_id: str, language: str = "zh-TW") -> str:
    """把內部 tool_id 對應到人類可讀、且符合 UI 語言的 display_name。

    用於 SSE progress 事件、錯誤訊息等「會被客戶端看到」的場合，避免洩漏
    內部 tool_id。

    語言解析優先序（i18n 修復，Bug#1）：
    1. 查 ``tool_name_translations._TRANSLATIONS[tool_id][language]``
    2. 缺譯 → fallback 到 ``_TOOLS_SEED`` 的繁中 display_name（向下相容舊行為）
    3. 連繁中都沒有 → fallback 詞（依 language，如 "tool" / "工具"）

    Args:
        tool_id: 內部工具名稱（如 "get_crypto_price"）。
        language: UI 語言代碼（zh-TW / zh-CN / en / ru）。未知語言 fallback zh-TW。

    Returns:
        人類可讀名稱；找不到時回中性 fallback 詞（依 language）。
    """
    if not tool_id:
        return _FALLBACK_TOOL_NAME.get(_normalize_lang(language), "工具")

    lang = _normalize_lang(language)

    # 1. 優先查多語翻譯表（en / zh-CN / ru）
    if lang != "zh-TW":
        from core.agents.tool_name_translations import get_translation

        translated = get_translation(tool_id, lang, "name")
        if translated:
            return translated

    # 2. fallback 到 _TOOLS_SEED 的繁中 display_name（zh-TW 原生 / 其他語缺譯）
    display_map = _load_tool_display_names()
    seed_name = display_map.get(tool_id)
    if seed_name:
        return seed_name

    # 2.5 非 seed 工具（MCP 等）：seed 沒有 → 查翻譯表的 zh-TW 條目。
    #     MCP 工具動態載入、不在 _TOOLS_SEED，zh-TW 直接放在翻譯表（4 語齊全）。
    if lang == "zh-TW":
        from core.agents.tool_name_translations import get_translation

        translated = get_translation(tool_id, "zh-TW", "name")
        if translated:
            return translated

    # 3. 連繁中都沒有 → 中性 fallback 詞
    return _FALLBACK_TOOL_NAME.get(lang, "工具")


def _normalize_lang(language: str | None) -> str:
    """把語言代碼正規化到支援清單，未知值 fallback zh-TW（產品主市場）。

    處理大小寫、前綴變體（如 "en-US" → "en"、"zh-HK" → "zh-TW"）。
    """
    if not language:
        return "zh-TW"
    lang = language.strip()
    if not lang:
        return "zh-TW"
    # 精確匹配
    if lang in _FALLBACK_TOOL_NAME:
        return lang
    lower = lang.lower()
    # 前綴匹配（en-US → en, ru-RU → ru）
    for supported in ("en", "ru"):
        if lower.startswith(supported):
            return supported
    # 中文變體一律 fallback zh-TW（zh-HK / zh-SG 等無獨立翻譯）
    if lower.startswith("zh"):
        # zh-CN 精確匹配保留
        if "cn" in lower or "hans" in lower:
            return "zh-CN"
        return "zh-TW"
    return "zh-TW"


def invalidate_display_name_cache() -> None:
    """測試專用：清掉 display-name cache（_TOOLS_SEED 改動後強制重建）。"""
    global _TOOL_DISPLAY_NAMES_CACHE
    _TOOL_DISPLAY_NAMES_CACHE = None


def is_dynamic_registry_active() -> bool:
    """便於測試：bootstrap 是否已注入動態清單。"""
    return bool(_REGISTERED_NAMES)


def reset_for_test() -> None:
    """測試專用：清空動態清單，回到 fallback 模式。"""
    global _REGISTERED_NAMES, _COMPILED_PATTERNS_CACHE, _COMPILED_BARE_NAME_CACHE
    _REGISTERED_NAMES = set()
    _COMPILED_PATTERNS_CACHE = None
    _COMPILED_BARE_NAME_CACHE = None


# ============================================================================
# G6 效能修復：預編譯 sanitizer regex（module-level cache）
#
# 舊版 _strip_tool_name_leaks 每次 call 都重新編譯 ~240 個 regex（base + _tool），
# 跑 ~1200 次 re.sub。對長回應是顯著 CPU。改良：所有 tool name 編進單一
# alternation regex，5 種泄露 pattern 各一個 compiled regex，總共 5 個。
#
# Cache invalidation：register_tool_names / reset_for_test 把 cache 設 None。
# ============================================================================

# 5 種 tool name 泄露 pattern（與 claw_loop._strip_tool_name_leaks 對應）。
# 用 {names} placeholder，建立時填入 alternation。
_LEAK_PATTERNS = [
    r"【\s*({names})\s*】",         # 【tool_name】
    r"\[\s*({names})\s*\]",         # [tool_name]
    r"[\(（]\s*({names})\s*[\)）]", # (tool_name) / （tool_name）
    r"`({names})`",                 # `tool_name`
    r"(來源|Source|資料來源)\s*[:：]\s*({names})\s*",  # 來源：tool_name
]

# 裸 tool name pattern（無包圍符號）。
# 風險考量：直接對所有 tool name 做 bare-name match 會誤殺（例如使用者訊息中
# 的隨機短詞）。因此：
# 1. 只對 len >= 8 的 long name 套用（避免 `tw_news` 等短詞誤觸）
# 2. 用 lookbehind/lookahead `(?<![\w])` `(?![\w])` 確保前後不是 word char
#    （避免抓到「my_get_crypto_price_thing」這種子字串）
# 3. 同時處理 _tool 變體
_BARE_NAME_MIN_LEN = 8
_COMPILED_PATTERNS_CACHE: list[re.Pattern] | None = None
_COMPILED_BARE_NAME_CACHE: re.Pattern | None = None


def _get_compiled_sanitizer_patterns() -> list[re.Pattern]:
    """取得預編譯的 sanitizer regex 清單。Cache miss 時重建。

    Returns:
        5 個 re.Pattern，依序對應 _LEAK_PATTERNS。
    """
    global _COMPILED_PATTERNS_CACHE
    if _COMPILED_PATTERNS_CACHE is not None:
        return _COMPILED_PATTERNS_CACHE

    base_names = get_registered_tool_names()
    # 自動加上 _tool 後綴變體（LangChain @tool handler 函式名）
    all_names = set(base_names)
    for n in base_names:
        if not n.endswith("_tool"):
            all_names.add(f"{n}_tool")

    # alternation：長 name 先 match（避免 'get_crypto_price' 被 'get_crypto'
    # 截斷誤判）。re.escape 防特殊字元。
    alt = "|".join(
        re.escape(n) for n in sorted(all_names, key=len, reverse=True)
    )

    patterns = [re.compile(p.format(names=alt)) for p in _LEAK_PATTERNS]
    _COMPILED_PATTERNS_CACHE = patterns
    return patterns


def _get_compiled_bare_name_pattern() -> re.Pattern:
    """取得預編譯的裸 tool name regex（無包圍符號的殘留）。

    Bare-name 移除的風險高於包圍式 pattern（容易誤殺），因此：
    - 只對 len >= ``_BARE_NAME_MIN_LEN`` 的 tool name 套用
    - 加 ``(?<!\\w)`` / ``(?!\\w)`` 邊界，前後不得為 word char

    與 5-pattern 清單獨立編譯，因為這個 pattern 用不同的 alternation
    （long names only）。

    Returns:
        一個 re.Pattern，match 到即 pure removal（取代為空字串）。
    """
    global _COMPILED_BARE_NAME_CACHE
    if _COMPILED_BARE_NAME_CACHE is not None:
        return _COMPILED_BARE_NAME_CACHE

    base_names = get_registered_tool_names()
    all_names = set(base_names)
    for n in base_names:
        if not n.endswith("_tool"):
            all_names.add(f"{n}_tool")

    # 只保留 len >= _BARE_NAME_MIN_LEN 的 name（避免誤殺短詞）
    long_names = [n for n in all_names if len(n) >= _BARE_NAME_MIN_LEN]
    if not long_names:
        # 沒有 long name 時，編一個永遠不 match 的 pattern（避免 None check）
        _COMPILED_BARE_NAME_CACHE = re.compile(r"(?!x)x")  # 永不 match
        return _COMPILED_BARE_NAME_CACHE

    alt = "|".join(
        re.escape(n) for n in sorted(long_names, key=len, reverse=True)
    )
    # (?<![\w]) 前、(?![\w]) 後：前後不可是 word char（letter/digit/underscore）
    _COMPILED_BARE_NAME_CACHE = re.compile(rf"(?<![\w])({alt})(?![\w])")
    return _COMPILED_BARE_NAME_CACHE


__all__ = [
    "register_tool_names",
    "get_registered_tool_names",
    "is_dynamic_registry_active",
    "reset_for_test",
    "_get_compiled_sanitizer_patterns",
    "_get_compiled_bare_name_pattern",
]
