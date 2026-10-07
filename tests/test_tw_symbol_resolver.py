"""Tests for TWSymbolResolver — uses mocked HTTP to avoid network dependency."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.tools.tw_symbol_resolver import TWSymbolResolver

# Minimal stock list for testing
MOCK_TWSE = [
    {
        "公司代號": "2330",
        "公司簡稱": "台積電",
        "公司全名": "台灣積體電路製造股份有限公司",
    },
    {"公司代號": "2317", "公司簡稱": "鴻海", "公司全名": "鴻海精密工業股份有限公司"},
]
# TPEx 的新端點欄位名是英文（與 TWSE 的中文欄位不同），且 Symbol 帶全形空白。
MOCK_TPEX = [
    {
        "SecuritiesCompanyCode": "6488",
        "CompanyAbbreviation": "環球晶",
        "CompanyName": "環球晶圓股份有限公司",
        "Symbol": "GWC　",
    },
]


def _make_mock_resp(data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = data
    return resp


@pytest.fixture
def resolver():
    with patch("httpx.get") as mock_get:
        # Return TWSE data for first call, TPEX for second
        mock_get.side_effect = [_make_mock_resp(MOCK_TWSE), _make_mock_resp(MOCK_TPEX)]
        r = TWSymbolResolver()
        r._get_stock_list()  # Pre-warm cache
    return r


def test_resolve_digit_code(resolver):
    """純數字 4 碼 → 補 .TW"""
    assert resolver.resolve("2330") == "2330.TW"


def test_resolve_already_tw(resolver):
    """已有 .TW suffix → 原樣返回"""
    assert resolver.resolve("2330.TW") == "2330.TW"


def test_resolve_already_two(resolver):
    """已有 .TWO suffix → 原樣返回"""
    assert resolver.resolve("6666.TWO") == "6666.TWO"


def test_resolve_chinese_name(resolver):
    """中文名稱模糊比對 → 返回 ticker"""
    result = resolver.resolve("台積電")
    assert result is not None
    assert "2330" in result


def test_resolve_english_name(resolver):
    """TSMC 無法精確比對時不報錯，返回 None 或有效 ticker"""
    result = resolver.resolve("TSMC")
    assert result is None or isinstance(result, str)


def test_resolve_unknown(resolver):
    """完全無法識別 → None"""
    result = resolver.resolve("XYZABC_DEFINITELY_NOT_A_STOCK_12345")
    assert result is None


def test_stock_list_cached(resolver):
    """快取已建立（不為空）"""
    assert resolver._cache is not None
    assert len(resolver._cache) > 0


# --- 上櫃（TPEx）------------------------------------------------------------
# 舊主機 openapi.tpex.org.tw 已下線（NXDOMAIN），線上每次都是 fetch error →
# 上櫃股票完全進不了模糊比對清單，中文名查上櫃股一律 resolve 不到。


def test_tpex_url_uses_live_host():
    """openapi.tpex.org.tw 已不存在，必須指向 www.tpex.org.tw 的新端點。"""
    assert "openapi.tpex.org.tw" not in TWSymbolResolver.TPEX_URL
    assert TWSymbolResolver.TPEX_URL.startswith("https://www.tpex.org.tw/openapi/")


def test_tpex_english_fields_are_parsed(resolver):
    """TPEx 新 schema 用英文欄位名，要能解析成上櫃 ticker（.TWO）。"""
    otc = [s for s in resolver._cache if s["ticker"].endswith(".TWO")]

    assert otc, "上櫃清單是空的 — TPEx 欄位沒對上"
    assert otc[0]["code"] == "6488"
    assert otc[0]["name"] == "環球晶"
    assert otc[0]["eng"] == "GWC"  # 全形空白要被 strip 掉


def test_resolve_otc_chinese_name(resolver):
    """中文名查上櫃股票 → .TWO ticker"""
    assert resolver.resolve("環球晶") == "6488.TWO"


def test_partial_failure_is_not_cached_for_a_full_day():
    """單一來源掛掉時只短快取，別讓半份清單（例如整個上櫃不見）撐滿 24 小時。"""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [_make_mock_resp(MOCK_TWSE), RuntimeError("boom")]
        r = TWSymbolResolver()
        r._get_stock_list()

    assert r._cache, "上市抓到了就該留著用"
    assert all(s["ticker"].endswith(".TW") for s in r._cache)

    remaining = r._cache_expiry - datetime.now(timezone.utc)
    assert remaining < timedelta(hours=1), "部分失敗卻用了完整 TTL"

    # 短 TTL 過後再呼叫要真的重打，把缺的上櫃補回來
    r._cache_expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [_make_mock_resp(MOCK_TWSE), _make_mock_resp(MOCK_TPEX)]
        r._get_stock_list()

    assert any(s["ticker"].endswith(".TWO") for s in r._cache)


def test_full_success_uses_full_ttl(resolver):
    """兩個來源都成功 → 用完整 24 小時 TTL。"""
    remaining = resolver._cache_expiry - datetime.now(timezone.utc)
    assert remaining > timedelta(hours=23)


def test_missing_fields_do_not_crash(resolver):
    """欄位缺漏或為 None 時略過該筆，不要炸掉整份清單。"""
    with patch("httpx.get") as mock_get:
        mock_get.side_effect = [
            _make_mock_resp([{"公司代號": None, "公司簡稱": None}]),
            _make_mock_resp([{"SecuritiesCompanyCode": "6488"}]),  # 缺簡稱
        ]
        r = TWSymbolResolver()
        assert r._get_stock_list() == []
