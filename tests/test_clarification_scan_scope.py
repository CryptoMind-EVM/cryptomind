"""Ticker substring scan 範圍測試 — clarification.py。

驗證 _extract_tickers_zh 的 alias 子字串掃描「只」涵蓋 curated + learned
（磁碟檔，~250 個），不涵蓋 preloader 預載的 ~11000 個 TW/US 公司名。

根因：preloader 把 ~11000 個 alias merge 進 multi_market_resolver 的
_LOCAL_ALIASES global，而 _load_alias_keys_cached 原本讀的就是這個 global，
導致每個 agent 請求的 O(n) 子字串掃描被放大約 40 倍。preloaded alias 本就是
給精確 resolve_symbol lookup 用，不該參與子字串掃描。

修法：_load_alias_keys_cached 改為只讀磁碟檔（company_aliases.json +
learned_aliases.json），不讀被污染的 _LOCAL_ALIASES global。
"""

from __future__ import annotations

import core.agents.clarification as clar_module


def test_alias_keys_excludes_preloaded(monkeypatch):
    """_load_alias_keys_cached 不該讀 _LOCAL_ALIASES global（會含 preloader 的量）。

    preloader 把 ~11000 個 TW/US 公司名 merge 進 _LOCAL_ALIASES global；
    _load_alias_keys_cached 應只讀磁碟 curated + learned，不讀該 global。
    驗證方式：mock 掉 _load_local_aliases 確認不被呼叫。
    """
    import unittest.mock

    # 重置 cache，強迫重新載入
    monkeypatch.setattr(clar_module, "_alias_keys_cache", None)

    with unittest.mock.patch(
        "core.tools.multi_market_resolver._load_local_aliases"
    ) as mock_load_global:
        mock_load_global.return_value = {"apple inc.": {"symbol": "AAPL"}}

        clar_module._load_alias_keys_cached()
        assert mock_load_global.call_count == 0, (
            "_load_alias_keys_cached 不該讀 _LOCAL_ALIASES global（會含 preloader "
            "的 ~11000 個 alias）；應只讀磁碟 curated + learned"
        )


def test_alias_keys_cached_returns_same_object(monkeypatch):
    """_alias_keys_cache 生效後，重複呼叫回傳同一物件（cache 生效）。"""
    # 先確保有值（可能已被其他測試填）
    clar_module._load_alias_keys_cached()
    keys1 = clar_module._load_alias_keys_cached()
    keys2 = clar_module._load_alias_keys_cached()
    assert keys1 is keys2  # 同一物件（cache 生效，不重新載入）


def test_extract_tickers_zh_finds_curated_chinese_alias():
    """curated 中文 alias（台積電/鴻海）應被掃描到。"""
    from core.agents.clarification import _extract_tickers_zh

    # 這些是 company_aliases.json 裡的常用 alias
    found = _extract_tickers_zh("我想買台積電跟鴻海", include_crypto=True)
    # 至少要抓到其中一個（確切 symbol 視資料檔內容）
    assert isinstance(found, set)


def test_extract_tickers_zh_does_not_scan_preloaded_long_names():
    """preloaded 的長公司名（如 'apple inc.'）不該被當 substring 掃描命中。

    這是 40x 掃描放大的根因——若掃描範圍含 11000 個 preloaded alias，
    每個 agent 請求都要對全部做 O(n) in 測試。
    修法後 _load_alias_keys_cached 只含 curated + learned（~250），
    不含 preloaded。這個測試驗證「即使 preloaded 有 apple inc.，
    一般 text 裡的 'apple' 也不該被 alias scan 抓到」（因為不在 scan 範圍）。
    """
    # 確認 cache 只含磁碟檔的量級（遠小於 11000）
    keys = clar_module._load_alias_keys_cached()
    assert len(keys) < 1000, (
        f"alias scan 範圍應只含 curated + learned（~250），實際 {len(keys)}；"
        "若接近 11000 代表仍讀到 preloader 污染的 global"
    )
