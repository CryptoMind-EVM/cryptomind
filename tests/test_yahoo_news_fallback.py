"""美股新聞區空白（2026-10-05 DANNY）：yfinance 的 ticker.news 對任何代號都回空，
改用 Yahoo 搜尋 API 的 news 區塊補（YahooFinanceProvider._search_news）。

不打網路：yf 與 httpx.get 都換成假的。
"""

from __future__ import annotations

import types

import pytest

from core.providers import yahoo_provider as mod

pytestmark = pytest.mark.unit


def _provider():
    return mod.YahooFinanceProvider(market="us")


class _FakeTicker:
    def __init__(self, news=None, raises=None):
        self._news = news
        self._raises = raises

    @property
    def news(self):
        if self._raises:
            raise self._raises
        return self._news


def _patch_yf(monkeypatch, ticker):
    monkeypatch.setattr(mod, "yf", types.SimpleNamespace(Ticker=lambda sym: ticker))


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


SEARCH_PAYLOAD = {
    "news": [
        {"title": " Apple lifts guidance ", "link": "https://finance.yahoo.com/news/a", "publisher": "Reuters", "providerPublishTime": 1791212340},
        {"title": "", "link": "https://finance.yahoo.com/news/empty-title", "publisher": "X"},
        {"title": "bad link", "link": "javascript:alert(1)", "publisher": "X"},
        {"title": "No time given", "link": "https://finance.yahoo.com/news/c", "publisher": "Motley Fool"},
    ]
}


def test_search_fallback_fills_in_when_yfinance_news_is_empty(monkeypatch):
    _patch_yf(monkeypatch, _FakeTicker(news=[]))
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        return _Resp(SEARCH_PAYLOAD)

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    items = _provider().get_news("AAPL", 5)

    assert [i["title"] for i in items] == ["Apple lifts guidance", "No time given"]  # 空標題、非 http 連結都丟掉
    first = items[0]
    assert first["symbol"] == "AAPL" and first["publisher"] == "Reuters"
    assert first["published"] == 1791212340 and first["pub_str"] == "2026-10-05 14:59"
    assert items[1]["published"] == 0 and items[1]["pub_str"] == ""
    assert calls[0][0].endswith("/v1/finance/search")
    assert calls[0][1] == {"q": "AAPL", "newsCount": 5, "quotesCount": 0}


def test_yfinance_news_wins_and_search_is_not_called(monkeypatch):
    raw = [{"content": {"title": "From yfinance", "canonicalUrl": {"url": "https://x.y/a"}, "provider": {"displayName": "Yahoo"}, "pubDate": "2026-10-05T10:00:00Z"}}]
    _patch_yf(monkeypatch, _FakeTicker(news=raw))

    def boom(*_a, **_k):
        raise AssertionError("yfinance 有新聞就不該打搜尋 API")

    monkeypatch.setattr(mod.httpx, "get", boom)
    items = _provider().get_news("AAPL", 5)
    assert [i["title"] for i in items] == ["From yfinance"]


def test_search_is_also_tried_when_yfinance_raises(monkeypatch):
    _patch_yf(monkeypatch, _FakeTicker(raises=RuntimeError("yahoo changed again")))
    monkeypatch.setattr(mod.httpx, "get", lambda *_a, **_k: _Resp(SEARCH_PAYLOAD))
    assert len(_provider().get_news("AAPL", 5)) == 2


def test_search_failure_returns_empty_not_an_error(monkeypatch):
    _patch_yf(monkeypatch, _FakeTicker(news=[]))

    def down(*_a, **_k):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(mod.httpx, "get", down)
    assert _provider().get_news("AAPL", 5) == []
    monkeypatch.setattr(mod.httpx, "get", lambda *_a, **_k: _Resp({}, status=429))
    assert _provider().get_news("MSFT", 5) == []
    monkeypatch.setattr(mod.httpx, "get", lambda *_a, **_k: _Resp({"news": None}))
    assert _provider().get_news("NVDA", 5) == []


def test_result_is_cached_so_the_search_api_is_hit_once(monkeypatch):
    _patch_yf(monkeypatch, _FakeTicker(news=[]))
    hits = []

    def fake_get(*_a, **_k):
        hits.append(1)
        return _Resp(SEARCH_PAYLOAD)

    monkeypatch.setattr(mod.httpx, "get", fake_get)
    provider = _provider()
    provider.get_news("TSLA", 5)
    provider.get_news("TSLA", 5)
    assert len(hits) == 1
