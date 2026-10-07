"""
test_ai_tool_correctness.py — AI 工具數值／代號正確性（全部合成資料，不打網路）

1. tw_technical_analysis 的 MACD signal / histogram 對調（pandas_ta 欄位順序是 MACD, MACDh, MACDs）
2. 港股代號沒補零（700 → 700.HK，yfinance 要 0700.HK）
3. A 股一律補 .SS（深圳 0/3 開頭要 .SZ）
4. YahooFinanceProvider 的 RSI 用 14 期 SMA，跟 tw_technical_analysis（Wilder）差到 ~10 點
5. _yfinance_exists 網路錯誤時把 False 快取 5 分鐘 → 合法代號被報查無
6. GoPlus 查無紀錄時說地址「may be safe」
7. TON 安全工具只收 EQ/UQ/0Q 開頭，合法的 Ef/Uf/kQ 與 raw 格式被擋
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def _ohlc(closes) -> pd.DataFrame:
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}
    )


def _random_walk(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return _ohlc(100 + np.cumsum(rng.normal(0, 2, n)))


class _FakeTicker:
    def __init__(self, hist: pd.DataFrame):
        self._hist = hist

    def history(self, *args, **kwargs):
        return self._hist.copy()


# ── 1. MACD 欄位 ───────────────────────────────────────────────────────────


def test_tw_technical_analysis_macd_uses_named_columns(monkeypatch):
    import pandas_ta as ta

    # 長多頭後急跌：MACD 線跌破 signal → signal 仍為正、histogram 轉負。
    # （macd - signal == histogram 在欄位對調時也成立，所以要看符號與具名欄位）
    up = [100 + i for i in range(100)]
    hist = _ohlc(up + [up[-1] - 3 * (i + 1) for i in range(8)])
    monkeypatch.setattr("yfinance.Ticker", lambda sym: _FakeTicker(hist))

    from core.tools.tw_stock_tools import tw_technical_analysis

    out = tw_technical_analysis.invoke({"ticker": "2330.TW"})
    macd = out["macd"]
    expected = ta.macd(hist["Close"]).iloc[-1]

    assert macd["signal"] > 0 and macd["histogram"] < 0
    assert macd["macd"] == round(float(expected["MACD_12_26_9"]), 4)
    assert macd["signal"] == round(float(expected["MACDs_12_26_9"]), 4)
    assert macd["histogram"] == round(float(expected["MACDh_12_26_9"]), 4)
    assert macd["histogram"] == pytest.approx(macd["macd"] - macd["signal"], abs=2e-4)


# ── 2. 港股補零 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("700", "0700.HK"),
        ("00700", "0700.HK"),  # 港交所 5 位官方寫法
        ("700.HK", "0700.HK"),
        ("5", "0005.HK"),
        ("9988", "9988.HK"),
        ("0700.HK", "0700.HK"),
    ],
)
def test_normalize_symbol_pads_hk_codes(raw, expected):
    from core.providers.base_provider import normalize_symbol

    assert normalize_symbol(raw, "hk") == expected


@pytest.mark.parametrize("raw", ["700", "700.HK", "00700"])
async def test_hk_klines_route_pads_code(monkeypatch, raw):
    from api.routers import hkstock

    seen: list[str] = []

    def _fake_klines(sym, interval, limit, retries):
        seen.append(sym)
        return []

    monkeypatch.setattr(hkstock, "fetch_klines_sync", _fake_klines)
    monkeypatch.setattr(hkstock, "_get_cache", lambda key: None)
    monkeypatch.setattr(hkstock, "_set_cache", lambda key, value, ttl=None: None)

    result = await hkstock.get_hk_klines.__wrapped__(None, raw)

    assert seen == ["0700.HK"]
    assert result["symbol"] == "0700.HK"


def test_earnings_symbol_pads_hk_codes():
    from core.daily_brief.calendar_sync import earnings_symbol

    assert earnings_symbol("700", "hk_stock") == "0700.HK"
    assert earnings_symbol("700.HK", "hk_stock") == "0700.HK"


# ── 3. A 股上海／深圳 ──────────────────────────────────────────────────────


def _entity(market: str, candidate: str | None, name: str = "x", conf: float = 0.9):
    return SimpleNamespace(
        market=market, candidate_symbol=candidate, asset_name=name, confidence=conf
    )


@pytest.mark.parametrize(
    ("market", "candidate", "expected"),
    [
        ("cn", "000858", "000858.SZ"),  # 深圳主板
        ("cn", "300750", "300750.SZ"),  # 創業板
        ("cn", "600519", "600519.SS"),  # 上海
        ("hk", "700", "0700.HK"),
        ("jp", "7203", "7203.T"),
    ],
)
def test_symbol_normalizer_routes_exchange_suffix(market, candidate, expected):
    from core.tools.symbol_normalizer import SymbolNormalizer

    n = SymbolNormalizer()
    checked: list[str] = []
    n._yfinance_exists = lambda sym: checked.append(sym) or True

    result = n._normalize_yfinance_suffix(_entity(market, candidate))

    assert checked == [expected]
    assert result.symbol == expected


def test_symbol_normalizer_low_confidence_fallback_routes_shenzhen():
    from core.tools.symbol_normalizer import SymbolNormalizer

    n = SymbolNormalizer()
    n._yfinance_exists = lambda sym: False
    n._yfinance_search = lambda query, market_suffix="": None

    result = n._normalize_yfinance_suffix(_entity("cn", "000858", conf=0.9))

    assert result.symbol == "000858.SZ"
    assert result.verified is False


def test_symbol_normalizer_cn_search_accepts_shenzhen(monkeypatch):
    from core.tools.symbol_normalizer import SymbolNormalizer

    quotes = [{"symbol": "WLYFF"}, {"symbol": "000858.SZ"}]
    monkeypatch.setattr(
        "yfinance.Search", lambda q, max_results=10: SimpleNamespace(quotes=quotes)
    )

    n = SymbolNormalizer()
    n._yfinance_exists = lambda sym: False

    result = n._normalize_yfinance_suffix(_entity("cn", None, name="五糧液"))

    assert result.symbol == "000858.SZ"


# ── 4. RSI 用 Wilder 平滑 ──────────────────────────────────────────────────


def _patch_provider_yf(monkeypatch, hist: pd.DataFrame):
    from core.providers import yahoo_provider

    monkeypatch.setattr(
        yahoo_provider, "yf", SimpleNamespace(Ticker=lambda sym: _FakeTicker(hist))
    )


def test_yahoo_provider_rsi_matches_pandas_ta(monkeypatch):
    import pandas_ta as ta

    from core.providers.yahoo_provider import YahooFinanceProvider

    hist = _random_walk()
    _patch_provider_yf(monkeypatch, hist)

    tech = YahooFinanceProvider("us").get_technicals("AAPL")

    expected = round(float(ta.rsi(hist["Close"], length=14).iloc[-1]), 1)
    assert tech["rsi"] == expected


def test_yahoo_provider_rsi_minimum_data(monkeypatch):
    from core.providers.yahoo_provider import YahooFinanceProvider

    # 剛好 15 根（14 個漲跌）→ 仍算得出 RSI
    _patch_provider_yf(monkeypatch, _ohlc([100 + (i % 3) - (i % 2) for i in range(15)]))
    tech = YahooFinanceProvider("us").get_technicals("MIN15")
    assert isinstance(tech["rsi"], float) and 0 <= tech["rsi"] <= 100

    # 不足 15 根 → 整包空（既有行為）
    _patch_provider_yf(monkeypatch, _ohlc(range(14)))
    assert YahooFinanceProvider("us").get_technicals("MIN14") == {}

    # 整段沒有漲跌 → RSI 無定義回 None，不是 100
    _patch_provider_yf(monkeypatch, _ohlc([100.0] * 30))
    assert YahooFinanceProvider("us").get_technicals("FLAT")["rsi"] is None


def test_pulse_technicals_rsi_matches_pandas_ta(monkeypatch):
    """市場脈動路由（港日韓印／A 股）走 yf_helpers，要跟 AI 工具同一套 RSI。"""
    import pandas_ta as ta

    from api.routers import yf_helpers

    hist = _random_walk(seed=11)
    monkeypatch.setattr(
        yf_helpers, "yf", SimpleNamespace(Ticker=lambda sym: _FakeTicker(hist))
    )

    tech = yf_helpers.fetch_technicals_sync("0700.HK")

    assert tech["rsi"] == round(float(ta.rsi(hist["Close"], length=14).iloc[-1]), 1)


@pytest.mark.parametrize(
    "module, pulse, quotes_fn, tech_fn, symbol",
    [
        (
            "hkstock",
            "get_hk_pulse",
            "_fetch_quotes_yahoo_batch",
            "_fetch_technicals_yahoo",
            "0700.HK",
        ),
        ("astock", "get_a_pulse", "_fetch_quotes_batch", "_fetch_technicals", "600519"),
        (
            "instock",
            "get_in_pulse",
            "_fetch_quotes_yahoo_batch",
            "_fetch_technicals_yahoo",
            "RELIANCE",
        ),
        (
            "krstock",
            "get_kr_pulse",
            "_fetch_quotes_yahoo_batch",
            "_fetch_technicals_yahoo",
            "005930",
        ),
        (
            "jpstock",
            "get_jp_pulse",
            "_fetch_quotes_yahoo_batch",
            "_fetch_technicals_yahoo",
            "7203",
        ),
    ],
)
@pytest.mark.parametrize("deep", [False, True])
async def test_pulse_renders_missing_indicators_as_na(
    monkeypatch, module, pulse, quotes_fn, tech_fn, symbol, deep
):
    """RSI 算不出來是 None（整段無漲跌／資料不足）：要點與 LLM prompt 要印 N/A，
    不是「RSI(14): None」；0 則照實顯示（不能用 `or` 一起吃掉）。"""
    import importlib
    from unittest.mock import AsyncMock

    from api.routers import deep_analysis_helper

    mod = importlib.import_module(f"api.routers.{module}")
    tech = {
        "rsi": None,
        "macd_histogram": 0.0,
        "ma20": None,
        "ma50": None,
        "52w_high": None,
        "52w_low": None,
    }
    quote = {"price": 100.0, "changePercent": 1.0, "name": "X", "currency": "USD"}
    monkeypatch.setattr(mod, quotes_fn, AsyncMock(return_value=[quote]))
    monkeypatch.setattr(mod, tech_fn, AsyncMock(return_value=tech))
    monkeypatch.setattr(mod, "fetch_news", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "_fetch_extras", AsyncMock(return_value={}))
    monkeypatch.setattr(mod, "_get_cache", lambda key: None)
    monkeypatch.setattr(mod, "_set_cache", lambda *a, **kw: None)
    monkeypatch.setattr(
        mod,
        "resolve_user_llm_credentials",
        AsyncMock(return_value={"provider": "openai", "api_key": "k", "model": "m"}),
    )
    deep_mock = AsyncMock(return_value={"source_mode": "on_demand"})
    monkeypatch.setattr(deep_analysis_helper, "get_deep_analysis", deep_mock)

    result = await getattr(mod, pulse).__wrapped__(
        None, symbol, deep, False, "zh-TW", None, {"user_id": "u1"}
    )

    if deep:
        text = deep_mock.call_args.kwargs["context"]
    else:
        text = "\n".join(result["report"]["key_points"])
    assert "RSI(14): N/A" in text
    assert "MACD Histogram: 0.0" in text
    assert "None" not in text, text
    # 回給前端的原始指標保留 null
    assert result["technical_indicators"]["rsi"] is None


# ── 5. 網路錯誤不快取成「不存在」 ──────────────────────────────────────────


def test_yfinance_exists_does_not_cache_network_errors(monkeypatch):
    from core.tools.symbol_normalizer import SymbolNormalizer

    class _Boom:
        def __init__(self, sym):
            raise ConnectionError("network down")

    monkeypatch.setattr("yfinance.Ticker", _Boom)
    n = SymbolNormalizer()
    assert n._yfinance_exists("0700.HK") is False

    # 網路恢復 → 立刻查得到，不被剛才的錯誤鎖 5 分鐘
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda sym: SimpleNamespace(info={"regularMarketPrice": 400.0}),
    )
    assert n._yfinance_exists("0700.HK") is True


def test_yfinance_search_does_not_cache_network_errors(monkeypatch):
    from core.tools.symbol_normalizer import SymbolNormalizer

    def _boom(q, max_results=10):
        raise ConnectionError("network down")

    monkeypatch.setattr("yfinance.Search", _boom)
    n = SymbolNormalizer()
    assert n._yfinance_search("Tencent", market_suffix=(".HK",)) is None

    monkeypatch.setattr(
        "yfinance.Search",
        lambda q, max_results=10: SimpleNamespace(quotes=[{"symbol": "0700.HK"}]),
    )
    assert n._yfinance_search("Tencent", market_suffix=(".HK",)) == "0700.HK"


# ── 6. GoPlus 查無紀錄 ≠ 安全 ──────────────────────────────────────────────


def test_address_safety_no_records_is_not_called_safe(monkeypatch):
    from core.tools.crypto_modules import goplus

    monkeypatch.setattr(
        goplus, "fetch_address_security_raw", lambda a, c=1: {"info": {}, "error": None}
    )

    result = goplus.check_address_safety.invoke({"address": "0x" + "a" * 40})

    assert "may be safe" not in result
    assert "no records" in result.lower()
    assert "does not mean" in result


# ── 7. TON 地址格式 ────────────────────────────────────────────────────────

_HASH = "b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"


def _patch_tonapi(monkeypatch):
    from core.tools.crypto_modules import ton_safety

    calls: list[str] = []

    def _fake_get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "verification": "whitelist",
                "holders_count": 5000,
                "metadata": {"symbol": "USDT", "name": "Tether USD"},
            },
        )

    monkeypatch.setattr(ton_safety.httpx, "get", _fake_get)
    return calls


@pytest.mark.parametrize(
    "address",
    [
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",  # bounceable 主鏈
        "UQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_p0p",  # non-bounceable
        "Ef-xE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_j-k",  # masterchain（-1）
        "Uf-xE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_mJh",
        "kQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_ntm",  # testnet bounceable
        "0QCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_iaj",  # testnet non-bounceable
        f"0:{_HASH}",
        f"-1:{_HASH}",
    ],
)
def test_ton_safety_accepts_all_valid_address_forms(monkeypatch, address):
    from core.tools.crypto_modules.ton_safety import assess_jetton_safety

    calls = _patch_tonapi(monkeypatch)

    safety = assess_jetton_safety(address)

    assert calls == [f"https://tonapi.io/v2/jettons/{address}"]
    assert safety.exists is True
    assert safety.verification == "whitelist"


@pytest.mark.parametrize(
    "address",
    [
        "",
        "hello",
        "0x" + "a" * 40,  # EVM
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sD",  # 47 字元
        "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id/sDs",  # 含 / 會打壞 URL path
        f"1:{_HASH}",  # 不存在的 workchain
        f"0:{_HASH[:-1]}",  # hash 少一碼
        f"0:{_HASH[:-1]}z",  # 非 hex
    ],
)
def test_ton_safety_rejects_invalid_address(monkeypatch, address):
    from core.tools.crypto_modules.ton_safety import assess_jetton_safety

    calls = _patch_tonapi(monkeypatch)

    safety = assess_jetton_safety(address)

    assert calls == []
    assert safety.verification == "unknown"
    assert safety.exists is False
