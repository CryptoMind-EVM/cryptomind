"""Unit tests for core/tools/web_search.py — SearXNG 引擎與 fallback chain。

Covers (per AGENTS.md test convention — success / reject / edge):
- search_searxng 成功路徑：JSON 欄位對映、publishedDate 正規化、max_results 截斷
- search_searxng 拒絕路徑：HTTP 4xx / timeout / 壞 JSON 一律回 []，不得往上拋
- search_web chain：BYOK 金鑰 → SearXNG → DuckDuckGo 的順序與跳過條件
  （SearXNG 是平台自架的免金鑰引擎，順位在所有 BYOK 之後、DuckDuckGo 之前）
"""

import json
from unittest.mock import patch

import httpx
import pytest

from core.tools import web_search

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_ENDPOINT = "http://searxng:8080"


def _fake_resp(status_code=200, json_body=None, text=""):
    """Build a fake httpx.Response for the SearXNG JSON API."""
    return httpx.Response(
        status_code=status_code,
        text=text or (json.dumps(json_body) if json_body is not None else ""),
        request=httpx.Request("GET", f"{_ENDPOINT}/search"),
    )


def _result(url, title="T", content="C", published=None, engine="brave"):
    return {
        "url": url,
        "title": title,
        "content": content,
        "publishedDate": published,
        "engine": engine,
    }


# ──────────────────────────────────────────────────────────────────────────────
# search_searxng — 成功路徑
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_searxng_maps_json_to_uniform_shape():
    """SearXNG 的 url/content/publishedDate 要對映成 chain 共用的 link/snippet/published_date。"""
    body = {
        "results": [
            _result(
                "https://example.com/a",
                title="BTC hits new high",
                content="Bitcoin rallied overnight.",
                published="2026-09-20T00:00:00+00:00",
                engine="google news",
            )
        ]
    }
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body=body)
    ):
        results = web_search.search_searxng("btc", _ENDPOINT)

    assert results == [
        {
            "title": "BTC hits new high",
            "link": "https://example.com/a",
            "snippet": "Bitcoin rallied overnight.",
            # ISO8601 只留日期，跟 Tavily/Serper 的輸出對齊
            "published_date": "2026-09-20",
        }
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-20T00:00:00+00:00", "2026-09-20"),  # 一般 ISO8601
        ("2026-09-20", "2026-09-20"),  # 已經只有日期
        (None, ""),  # 引擎沒給（general 類結果常見）
        ("", ""),  # 空字串
    ],
)
def test_searxng_normalizes_published_date(raw, expected):
    """沒有日期的結果要留空字串——模型靠這個欄位判斷資訊新舊，不能塞垃圾。"""
    body = {"results": [_result("https://example.com/a", published=raw)]}
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body=body)
    ):
        results = web_search.search_searxng("q", _ENDPOINT)

    assert results[0]["published_date"] == expected


@pytest.mark.unit
def test_searxng_truncates_to_max_results():
    """SearXNG 合併多引擎後一次回幾十筆，且沒有 max_results 參數——截斷在我們這側。"""
    body = {"results": [_result(f"https://example.com/{i}") for i in range(30)]}
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body=body)
    ):
        results = web_search.search_searxng("q", _ENDPOINT, max_results=3)

    assert len(results) == 3
    assert results[0]["link"] == "https://example.com/0"


@pytest.mark.unit
def test_searxng_requests_json_format_and_both_categories():
    """format=json 是命脈（settings.yml 沒開就會 403）；news 類別帶回發布日期。"""
    body = {"results": []}
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body=body)
    ) as mock_get:
        web_search.search_searxng("q", _ENDPOINT)

    params = mock_get.call_args.kwargs["params"]
    assert params["format"] == "json"
    assert params["categories"] == "general,news"
    assert params["q"] == "q"


@pytest.mark.unit
def test_searxng_endpoint_trailing_slash_does_not_double_up():
    """SEARXNG_URL 結尾帶不帶 / 都要組出同一個路徑。"""
    body = {"results": []}
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body=body)
    ) as mock_get:
        web_search.search_searxng("q", "http://searxng:8080/")

    assert mock_get.call_args.args[0] == "http://searxng:8080/search"


# ──────────────────────────────────────────────────────────────────────────────
# search_searxng — 拒絕 / 邊界路徑（一律回 []，讓 chain 往下退）
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("status", [403, 429, 500])
def test_searxng_http_error_returns_empty(status):
    """403 = settings.yml 沒開 json format；500 = 實例掛了。都該安靜退場。"""
    with patch(
        "core.tools.web_search.httpx.get",
        return_value=_fake_resp(status_code=status, text="nope"),
    ):
        assert web_search.search_searxng("q", _ENDPOINT) == []


@pytest.mark.unit
def test_searxng_timeout_returns_empty():
    """SearXNG 要等多個上游引擎，逾時比其他引擎常見——不能把例外炸到 agent。"""
    with patch(
        "core.tools.web_search.httpx.get",
        side_effect=httpx.TimeoutException("too slow"),
    ):
        assert web_search.search_searxng("q", _ENDPOINT) == []


@pytest.mark.unit
def test_searxng_malformed_json_returns_empty():
    with patch(
        "core.tools.web_search.httpx.get",
        return_value=_fake_resp(text="<html>not json</html>"),
    ):
        assert web_search.search_searxng("q", _ENDPOINT) == []


@pytest.mark.unit
def test_searxng_missing_results_key_returns_empty():
    """所有引擎都被擋時 SearXNG 會回 200 但沒有 results。"""
    with patch(
        "core.tools.web_search.httpx.get", return_value=_fake_resp(json_body={})
    ):
        assert web_search.search_searxng("q", _ENDPOINT) == []


# ──────────────────────────────────────────────────────────────────────────────
# search_web — fallback chain 順序
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def no_byok_keys():
    """所有 BYOK provider 都沒金鑰——模擬免費使用者。"""
    with patch("core.tools.key_resolver.resolve_tool_key", return_value=None):
        yield


@pytest.mark.unit
def test_chain_uses_searxng_before_duckduckgo(monkeypatch, no_byok_keys):
    """沒有任何 BYOK 金鑰時，預設引擎是 SearXNG 而不是 DuckDuckGo。"""
    monkeypatch.setenv("SEARXNG_URL", _ENDPOINT)
    hit = [
        {"title": "t", "link": "https://e.com", "snippet": "s", "published_date": ""}
    ]

    with (
        patch("core.tools.web_search.search_searxng", return_value=hit) as mock_searxng,
        patch("core.tools.web_search.search_duckduckgo") as mock_ddg,
    ):
        assert web_search.search_web("q", max_results=5) == hit

    mock_searxng.assert_called_once_with("q", _ENDPOINT, max_results=5)
    mock_ddg.assert_not_called()


@pytest.mark.unit
def test_chain_skips_searxng_when_env_unset(monkeypatch, no_byok_keys):
    """沒部署 SearXNG（SEARXNG_URL 未設）時整段跳過，行為回到原本的 DuckDuckGo。"""
    monkeypatch.delenv("SEARXNG_URL", raising=False)

    with (
        patch("core.tools.web_search.search_searxng") as mock_searxng,
        patch("core.tools.web_search.search_duckduckgo", return_value=[]) as mock_ddg,
    ):
        web_search.search_web("q")

    mock_searxng.assert_not_called()
    mock_ddg.assert_called_once()


@pytest.mark.unit
def test_chain_skips_searxng_when_env_blank(monkeypatch, no_byok_keys):
    """邊界：env 設成空白字串等同沒設（部署時常見的手滑）。"""
    monkeypatch.setenv("SEARXNG_URL", "   ")

    with (
        patch("core.tools.web_search.search_searxng") as mock_searxng,
        patch("core.tools.web_search.search_duckduckgo", return_value=[]),
    ):
        web_search.search_web("q")

    mock_searxng.assert_not_called()


@pytest.mark.unit
def test_chain_falls_back_to_duckduckgo_when_searxng_empty(monkeypatch, no_byok_keys):
    """SearXNG 全空（上游引擎都被擋）時仍要有結果——DuckDuckGo 是最後保險。"""
    monkeypatch.setenv("SEARXNG_URL", _ENDPOINT)
    ddg_hit = [{"title": "t", "link": "https://e.com", "snippet": "s"}]

    with (
        patch("core.tools.web_search.search_searxng", return_value=[]),
        patch(
            "core.tools.web_search.search_duckduckgo", return_value=ddg_hit
        ) as mock_ddg,
    ):
        assert web_search.search_web("q") == ddg_hit

    mock_ddg.assert_called_once()


@pytest.mark.unit
def test_chain_prefers_byok_tavily_over_searxng(monkeypatch):
    """使用者自帶金鑰時仍優先用 Tavily——自架實例是公共資源，能省則省。"""
    monkeypatch.setenv("SEARXNG_URL", _ENDPOINT)
    tavily_hit = [
        {"title": "t", "link": "https://e.com", "snippet": "s", "published_date": ""}
    ]

    def _resolve(provider, official_env=None, user_id=None):
        return "tvly-key" if provider == "tavily" else None

    with (
        patch("core.tools.key_resolver.resolve_tool_key", side_effect=_resolve),
        patch("core.tools.web_search.search_tavily", return_value=tavily_hit),
        patch("core.tools.web_search.search_searxng") as mock_searxng,
        patch("core.tools.web_search.search_duckduckgo") as mock_ddg,
    ):
        assert web_search.search_web("q") == tavily_hit

    mock_searxng.assert_not_called()
    mock_ddg.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    "provider,engine_fn",
    [("serper", "search_serper"), ("fmp", "search_fmp")],
)
def test_chain_uses_byok_search_providers_before_searxng(
    monkeypatch, provider, engine_fn
):
    """serper / fmp 有金鑰時要真的被用到。

    這兩段在 2026-09-22 前是死碼——provider 沒註冊，金鑰存不進去，
    resolve_tool_key 永遠回 None。守著它們不要再退回去。
    """
    monkeypatch.setenv("SEARXNG_URL", _ENDPOINT)
    hit = [
        {"title": "t", "link": "https://e.com", "snippet": "s", "published_date": ""}
    ]

    def _resolve(p, official_env=None, user_id=None):
        return "key-123" if p == provider else None

    with (
        patch("core.tools.key_resolver.resolve_tool_key", side_effect=_resolve),
        patch(f"core.tools.web_search.{engine_fn}", return_value=hit) as mock_engine,
        patch("core.tools.web_search.search_searxng") as mock_searxng,
        patch("core.tools.web_search.search_duckduckgo") as mock_ddg,
    ):
        assert web_search.search_web("q") == hit

    mock_engine.assert_called_once()
    mock_searxng.assert_not_called()
    mock_ddg.assert_not_called()


@pytest.mark.unit
def test_chain_falls_through_byok_failures_to_searxng(monkeypatch):
    """有金鑰但三家都回空（額度用完/服務掛了）→ 仍要退到 SearXNG，不是直接 DDG。"""
    monkeypatch.setenv("SEARXNG_URL", _ENDPOINT)
    searxng_hit = [
        {"title": "s", "link": "https://e.com", "snippet": "s", "published_date": ""}
    ]

    with (
        patch("core.tools.key_resolver.resolve_tool_key", return_value="key-123"),
        patch("core.tools.web_search.search_tavily", return_value=[]),
        patch("core.tools.web_search.search_serper", return_value=[]),
        patch("core.tools.web_search.search_fmp", return_value=[]),
        patch("core.tools.web_search.search_searxng", return_value=searxng_hit),
        patch("core.tools.web_search.search_duckduckgo") as mock_ddg,
    ):
        assert web_search.search_web("q") == searxng_hit

    mock_ddg.assert_not_called()


# ── FMP 結果對映（2026-09-23：link 欄位塞交易所名稱的假來源）────────────────


def _fmp_resp(rows):
    return httpx.Response(
        status_code=200,
        text=json.dumps(rows),
        request=httpx.Request("GET", "https://financialmodelingprep.com/api/v3/search"),
    )


@pytest.mark.unit
def test_fmp_never_puts_exchange_name_in_link():
    """link 會被 web_search_tool 印成 "Source: xxx"。

    原本塞的是 r["exchange"]（交易所名稱），模型會看到「Source: NASDAQ Global
    Select」這種假網址，甚至拿去餵 fetch_url。FMP 回的是代號識別不是網頁，
    寧可留空也不要給假的。
    """
    rows = [
        {
            "symbol": "TSM",
            "name": "Taiwan Semiconductor Manufacturing Company Limited",
            "currency": "USD",
            "stockExchange": "NASDAQ Global Select",
            "exchangeShortName": "NASDAQ",
        }
    ]
    with patch("core.tools.web_search.httpx.get", return_value=_fmp_resp(rows)):
        results = web_search.search_fmp("台積電", "key-123")

    assert results[0]["link"] == ""
    # 交易所資訊要留在 snippet（該待的地方），不能不見
    assert "NASDAQ" in results[0]["snippet"]
    assert "TSM" in results[0]["snippet"]
    assert "TSM" in results[0]["title"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "row,expected",
    [
        ({"symbol": "A", "name": "N", "exchangeShortName": "NYSE"}, "NYSE"),
        ({"symbol": "A", "name": "N", "stockExchange": "New York"}, "New York"),
        ({"symbol": "A", "name": "N", "exchange": "AMEX"}, "AMEX"),
    ],
)
def test_fmp_accepts_any_exchange_field_name(row, expected):
    """FMP 各版本欄位名稱不同，三種都要認得。"""
    with patch("core.tools.web_search.httpx.get", return_value=_fmp_resp([row])):
        results = web_search.search_fmp("q", "key-123")
    assert expected in results[0]["snippet"]


@pytest.mark.unit
def test_fmp_missing_exchange_still_returns_usable_row():
    """邊界：什麼交易所欄位都沒有時不能炸，也不能留下尾巴。"""
    with patch(
        "core.tools.web_search.httpx.get",
        return_value=_fmp_resp([{"symbol": "A", "name": "N"}]),
    ):
        results = web_search.search_fmp("q", "key-123")
    assert results[0]["snippet"] == "N (A)"
    assert not results[0]["snippet"].endswith("—")


@pytest.mark.unit
def test_fmp_truncates_to_max_results():
    rows = [{"symbol": f"S{i}", "name": f"N{i}"} for i in range(10)]
    with patch("core.tools.web_search.httpx.get", return_value=_fmp_resp(rows)):
        assert len(web_search.search_fmp("q", "key-123", max_results=3)) == 3


# ── 工具輸出格式 ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_tool_output_omits_empty_source_line():
    """沒有 URL 就不要印空的 "Source:"——模型會以為來源被截斷。"""
    hit = [{"title": "T", "link": "", "snippet": "S", "published_date": ""}]
    with patch("core.tools.web_search.search_web", return_value=hit):
        out = web_search.web_search_tool.invoke({"query": "q"})

    assert "Source:" not in out
    assert "**T**" in out


@pytest.mark.unit
def test_tool_output_keeps_source_line_when_url_present():
    hit = [
        {"title": "T", "link": "https://e.com/a", "snippet": "S", "published_date": ""}
    ]
    with patch("core.tools.web_search.search_web", return_value=hit):
        out = web_search.web_search_tool.invoke({"query": "q"})

    assert "Source: https://e.com/a" in out
