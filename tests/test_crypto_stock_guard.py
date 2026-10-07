"""Tests for crypto-symbol misroute guard.

Regression: LLM 偶爾把 BTC/ETH 丟給美股/台股/港股工具，yfinance 把 BTC
解析成 Grayscale ETF 或 penny stock，導致回傳 $0.00001047 的垃圾價格。
所有股票工具入口現在都用 is_crypto_symbol() 攔截，回附正確工具提示的 error。

這些測試不需要網路（guard 在呼叫 provider 前短路）。
"""

from core.tools.global_stock_tools import (
    global_stock_fundamentals,
    global_stock_news,
    global_stock_price,
    global_stock_snapshot,
    global_stock_technical,
)
from core.tools.helpers import crypto_misroute_error, is_crypto_symbol
from core.tools.tw_stock_tools import (
    tw_fundamentals,
    tw_institutional,
    tw_news,
    tw_pe_ratio,
    tw_stock_price,
    tw_stock_snapshot,
    tw_technical_analysis,
)
from core.tools.us_stock_tools import (
    us_earnings,
    us_fundamentals,
    us_insider_transactions,
    us_institutional_holders,
    us_news,
    us_stock_price,
    us_stock_snapshot,
    us_technical_analysis,
)

# ============ is_crypto_symbol 單元 ============


def test_is_crypto_symbol_matches_major_coins():
    """主流幣（含大小寫、含後綴）應被識別為 crypto。"""
    for sym in ("BTC", "btc", "ETH", "SOL", "TON", "DOGE", "PEPE"):
        assert is_crypto_symbol(sym), f"{sym} 應為 crypto"


def test_is_crypto_symbol_matches_with_yfinance_suffix():
    """LLM 可能傳 BTC-USD / BTC.TW / btc=usd，base 去後綴後仍應命中。"""
    for sym in ("BTC-USD", "btc=usd", "BTC.TW", "ETH.NS", "SOL.HK"):
        assert is_crypto_symbol(sym), f"{sym} 應為 crypto"


def test_is_crypto_symbol_rejects_real_stocks():
    """真股票代號不可被誤判為 crypto。"""
    for sym in ("AAPL", "TSLA", "NVDA", "2330", "0700.HK", "7203.T", "005930.KS"):
        assert not is_crypto_symbol(sym), f"{sym} 不應為 crypto"


def test_is_crypto_symbol_handles_empty_and_garbage():
    """空字串/None/無意義輸入不可 crash，應回 False。"""
    for sym in ("", None, "   ", "HELLO"):
        assert not is_crypto_symbol(sym)


def test_crypto_misroute_error_format():
    """錯誤回應含 error/hint/symbol 三欄，且 hint 指向 get_crypto_price。"""
    err = crypto_misroute_error("BTC", "US stocks")
    assert err["error"] == "BTC is a cryptocurrency and cannot be queried with the US stocks tool."
    assert "get_crypto_price" in err["hint"]
    assert "BTC" in err["hint"]
    assert err["symbol"] == "BTC"


def test_crypto_misroute_error_strips_suffix():
    """BTC-USD 傳入時，base(BTC) 進錯誤訊息而非原始 BTC-USD。"""
    err = crypto_misroute_error("BTC-USD", "TW stocks")
    assert err["error"].startswith("BTC is a cryptocurrency")
    assert err["symbol"] == "BTC-USD"


# ============ 美股工具：crypto 應短路回 error，不碰 yfinance ============


def test_us_stock_price_rejects_crypto():
    """BTC 丟給 us_stock_price 必須回 error，不可回 ETF 垃圾價。"""
    result = us_stock_price.invoke({"symbol": "BTC"})
    assert "error" in result
    assert "get_crypto_price" in result["hint"]
    assert "BTC" in result["error"]


def test_all_us_stock_tools_reject_crypto():
    """8 個美股工具入口全部攔截 BTC。"""
    tools = [
        us_stock_price,
        us_technical_analysis,
        us_fundamentals,
        us_earnings,
        us_news,
        us_institutional_holders,
        us_insider_transactions,
        us_stock_snapshot,
    ]
    for t in tools:
        result = t.invoke({"symbol": "BTC"})
        # news 回 list，其餘回 dict；統一取第一個 error 物件
        if isinstance(result, list):
            result = result[0]
        assert "get_crypto_price" in result["hint"], f"{t.name} 沒攔截 crypto"


def test_us_stock_tools_pass_real_stocks():
    """真美股代號不該被 guard 攔（交給 provider 正常查）。這裡只驗 guard 不誤殺。"""
    # 用 mock provider 避免網路：直接驗 guard 沒觸發 = result 不含 misroute hint
    # 由於真實查會碰網路，這裡只驗非 crypto symbol 的 guard 判斷正確
    assert not is_crypto_symbol("AAPL")
    assert not is_crypto_symbol("TSLA")


# ============ 台股工具：crypto 應短路 ============


def test_tw_stock_tools_reject_crypto():
    """台股 ticker/code 工具入口攔截 BTC。"""
    ticker_tools = [
        tw_stock_price,
        tw_technical_analysis,
        tw_fundamentals,
        tw_institutional,
        tw_stock_snapshot,
    ]
    for t in ticker_tools:
        result = t.invoke({"ticker": "BTC"})
        if isinstance(result, list):
            result = result[0]
        assert "get_crypto_price" in result.get("hint", ""), f"{t.name} 沒攔截 crypto"

    code_tools = [tw_pe_ratio]
    for t in code_tools:
        result = t.invoke({"code": "BTC"})
        assert "get_crypto_price" in result["hint"], f"{t.name} 沒攔截 crypto"


def test_tw_news_rejects_crypto():
    """tw_news 回 list，crypto 時應回 [error_dict]。"""
    result = tw_news.invoke({"ticker": "ETH"})
    assert isinstance(result, list)
    assert "get_crypto_price" in result[0]["hint"]


def test_tw_optional_code_tools_allow_empty():
    """code 預設為空字串（全市場查詢）不可被 guard 攔。"""
    # 空 code 應通過 guard（is_crypto_symbol("") == False），交給 provider
    # 這裡只驗 guard 判斷
    assert not is_crypto_symbol("")


# ============ 全球股市工具：crypto 應短路 ============


def test_global_stock_tools_reject_crypto():
    """全球股市工具（港股/日股/韓股/印股）攔截 BTC，且錯誤訊息含對應市場名。"""
    tools = [
        global_stock_price,
        global_stock_technical,
        global_stock_fundamentals,
        global_stock_snapshot,
    ]
    for market, label in [("hk", "Hong Kong"), ("jp", "Japan"), ("kr", "Korea"), ("in", "India")]:
        for t in tools:
            result = t.invoke({"symbol": "BTC", "market": market})
            assert "get_crypto_price" in result["hint"], f"{t.name}/{market} 沒攔截"
            assert label in result["error"], f"{t.name}/{market} 缺市場名"


def test_global_stock_news_rejects_crypto():
    """global_stock_news 回 list，crypto 時回 [error_dict]。"""
    result = global_stock_news.invoke({"symbol": "BTC", "market": "hk"})
    assert isinstance(result, list)
    assert "get_crypto_price" in result[0]["hint"]
