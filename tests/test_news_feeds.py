"""日／韓／A／港股、商品、外匯的免費新聞與公告來源（api/routers/news_feeds.py）。

2026-10-05：Yahoo 對這些市場的代號幾乎沒有新聞，新聞區一直是空的，補 Google News RSS、
日股 TDnet 公告、A 股東方財富公告（都免費、免金鑰）。這裡只測解析與組查詢，不打網路。
"""

import json
import re
from pathlib import Path

import pytest

from api.routers import news_feeds, yf_helpers

ROOT = Path(__file__).resolve().parent.parent

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>x</title>
<item><title>日経平均が続伸 - 日本経済新聞</title><link>https://news.google.com/rss/articles/a</link>
<pubDate>Thu, 01 Oct 2026 02:58:31 GMT</pubDate><source url="https://www.nikkei.com">日本経済新聞</source></item>
<item><title>トヨタ自動車(株)【7203】：掲示板 - Yahoo!ファイナンス</title><link>https://news.google.com/rss/articles/b</link>
<pubDate>Fri, 02 Oct 2026 07:00:00 GMT</pubDate><source url="https://finance.yahoo.co.jp">Yahoo!ファイナンス</source></item>
<item><title>トヨタ、新型車を発表 - 朝日新聞</title><link>https://news.google.com/rss/articles/c</link>
<pubDate>Fri, 02 Oct 2026 01:00:00 GMT</pubDate><source url="https://www.asahi.com">朝日新聞</source></item>
<item><title>壞連結</title><link>javascript:alert(1)</link><source>X</source></item>
<item><title></title><link>https://example.com/x</link></item>
</channel></rss>""".encode()


def test_google_news_parses_sorts_and_filters():
    items = news_feeds.parse_google_news(RSS, "7203.T", 10)
    assert [i["title"] for i in items] == ["トヨタ、新型車を発表", "日経平均が続伸"]  # 新→舊
    first = items[0]
    assert first["publisher"] == "朝日新聞"  # 來源名稱附在每一則上
    assert first["feed"] == "google_news"
    assert first["symbol"] == "7203.T"
    assert first["pub_str"] == "2026-10-02 01:00"
    assert first["url"].startswith("https://")  # javascript: 之類的連結一律丟掉


def test_google_news_respects_limit_and_rejects_bad_xml():
    assert len(news_feeds.parse_google_news(RSS, "X", 1)) == 1
    assert news_feeds.parse_google_news(b"<html>not rss", "X", 5) == []
    assert news_feeds.parse_google_news(b"", "X", 5) == []


def test_google_news_rejects_entity_declarations():
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title><link>https://x.y</link></item></channel></rss>'
    assert news_feeds.parse_google_news(bomb, "X", 5) == []


def test_tdnet_strips_redirect_and_reads_jst():
    payload = json.dumps(
        {
            "items": [
                {
                    "Tdnet": {
                        "title": "自己株式の取得状況に関するお知らせ",
                        "document_url": "https://webapi.yanoshin.jp/rd.php?https://www.release.tdnet.info/inbs/140120260903000001.pdf",
                        "pubdate": "2026-09-03 15:30:00",
                    }
                },
                {"Tdnet": {"title": "", "document_url": "https://x.y/z.pdf"}},
            ]
        }
    ).encode()
    items = news_feeds.parse_tdnet(payload, "7203.T", 5)
    assert len(items) == 1
    assert items[0]["url"] == "https://www.release.tdnet.info/inbs/140120260903000001.pdf"
    assert items[0]["feed"] == "tdnet"
    assert items[0]["pub_str"] == "2026-09-03 06:30"  # 15:30 JST = 06:30 UTC
    assert news_feeds.parse_tdnet(b"not json", "7203.T", 5) == []


@pytest.mark.parametrize("wrap", ["{body}", "jQuery123({body});"])
def test_eastmoney_parses_plain_and_jsonp(wrap):
    body = json.dumps(
        {
            "data": {
                "list": [
                    {"title": "贵州茅台:2026年半年度报告摘要", "art_code": "AN202608141827994403", "notice_date": "2026-08-15 00:00:00"},
                    {"title": "壞的", "art_code": "../../etc", "notice_date": "2026-08-15"},
                ]
            }
        }
    )
    items = news_feeds.parse_eastmoney(wrap.format(body=body).encode(), "600519.SS", 5)
    assert len(items) == 1  # art_code 不是英數字的丟掉（組連結用）
    assert items[0]["url"] == "https://data.eastmoney.com/notices/detail/600519/AN202608141827994403.html"
    assert items[0]["feed"] == "eastmoney"
    assert news_feeds.parse_eastmoney(b"", "600519.SS", 5) == []


NAMES = {
    "7203.T": {"zh": "豐田汽車", "en": "Toyota", "local": "トヨタ自動車"},
    "005930.KS": {"zh": "三星電子", "en": "Samsung", "local": "삼성전자"},
    "600519.SS": {"zh": "貴州茅台", "en": "Kweichow Moutai"},
}


def test_query_uses_local_language_name_and_code():
    q = news_feeds._query_for
    assert q("jp", "7203.T", NAMES, "zh-TW") == "トヨタ自動車 7203"
    assert q("kr", "005930.KS", NAMES, "zh-TW") == "삼성전자 005930"
    assert q("cn", "600519.SS", NAMES, "zh-TW") == "貴州茅台 600519"
    assert q("jp", "9999.T", NAMES, "zh-TW") == "9999"  # 表外的代號：只用代號


def test_query_for_topics_follows_language():
    q = news_feeds._query_for
    assert q("jp", "^N225", NAMES, "zh-TW") == "日経平均 株価"  # 指數：當地語言
    assert q("commodity", "GC=F", None, "zh-TW") == "黃金 金價 國際"
    assert q("commodity", "GC=F", None, "en") == "gold price"
    assert q("commodity", "GC=F", None, "ru") == "gold price"  # 非中文一律用英文版
    assert q("commodity", "GC=F", None, "zh-CN") == "黃金 金價 國際"
    assert q("forex", "USDJPY=X", None, "zh-TW") == "USD JPY 匯率"
    assert q("forex", "USDJPY=X", None, "en") == "USD/JPY exchange rate"
    assert q("commodity", "XYZ=F", None, "zh-TW") is None  # 沒有搜尋詞就不查


async def test_feed_failures_are_swallowed(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(news_feeds, "google_news", boom)
    monkeypatch.setattr(news_feeds, "tdnet_disclosures", boom)
    assert await news_feeds.fetch_feed_news("jp", "7203.T", NAMES) == []


async def test_only_japan_and_china_stocks_get_official_feeds(monkeypatch):
    calls = []

    async def fake_google(*_a, **_k):
        return []

    async def fake_tdnet(symbol, limit):
        calls.append(("tdnet", symbol))
        return []

    async def fake_em(symbol, limit):
        calls.append(("eastmoney", symbol))
        return []

    monkeypatch.setattr(news_feeds, "google_news", fake_google)
    monkeypatch.setattr(news_feeds, "tdnet_disclosures", fake_tdnet)
    monkeypatch.setattr(news_feeds, "eastmoney_announcements", fake_em)
    await news_feeds.fetch_feed_news("jp", "7203.T", NAMES)
    await news_feeds.fetch_feed_news("jp", "^N225", NAMES)  # 指數沒有公司公告
    await news_feeds.fetch_feed_news("cn", "600519.SS", NAMES)
    await news_feeds.fetch_feed_news("cn", "000001.SS", NAMES)  # 上證指數也是 .SS，但 6 碼才查（見下）
    await news_feeds.fetch_feed_news("kr", "005930.KS", NAMES)
    assert ("tdnet", "7203.T") in calls and ("tdnet", "^N225") not in calls
    assert ("eastmoney", "600519.SS") in calls
    assert not any(kind == "tdnet" and sym.endswith(".KS") for kind, sym in calls)


async def test_eastmoney_skips_non_six_digit_codes(monkeypatch):
    async def never(*_a, **_k):
        raise AssertionError("不該打網路")

    monkeypatch.setattr(news_feeds, "_get", never)
    assert await news_feeds.eastmoney_announcements("^HSI", 5) == []


async def test_fetch_news_merges_yahoo_and_feeds_dedup_sorted(monkeypatch):
    monkeypatch.setattr(
        yf_helpers,
        "fetch_news_sync",
        lambda s, n: [{"symbol": s, "title": "same", "published": 100, "publisher": "Yahoo"}],
    )

    async def fake_feed(market, symbol, names, lang, limit):
        assert (market, lang) == ("jp", "en")
        return [
            {"symbol": symbol, "title": "same", "published": 300, "feed": "google_news"},  # 跟 Yahoo 同標題：去重
            {"symbol": symbol, "title": "newer", "published": 500, "feed": "tdnet"},
        ]

    monkeypatch.setattr(news_feeds, "fetch_feed_news", fake_feed)
    merged = await yf_helpers.fetch_news(["7203.T"], limit_per=5, market="jp", names=NAMES, lang="en")
    assert [n["title"] for n in merged] == ["newer", "same"]


async def test_fetch_news_without_market_is_yahoo_only(monkeypatch):
    monkeypatch.setattr(yf_helpers, "fetch_news_sync", lambda s, n: [{"symbol": s, "title": "y", "published": 1}])

    async def boom(*_a, **_k):
        raise AssertionError("沒給 market 不該碰免費來源（美股／台股行為不變）")

    monkeypatch.setattr(news_feeds, "fetch_feed_news", boom)
    assert [n["title"] for n in await yf_helpers.fetch_news(["AAPL"], 5)] == ["y"]


class _FI:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_quote_extras_normal_and_zero_volume_index():
    fi = _FI(last_volume=9210000.0, day_high=2897.04, day_low=2867.5, year_high=4000.0, year_low=2686.0)
    assert yf_helpers.quote_extras(fi, 1) == {
        "volume": 9210000,
        "dayHigh": 2897.0,
        "dayLow": 2867.5,
        "yearHigh": 4000.0,
        "yearLow": 2686.0,
    }
    index = _FI(last_volume=0, day_high=70072.1, day_low=69050.2, year_high=72831.7, year_low=46544.1)
    assert yf_helpers.quote_extras(index, 1)["volume"] is None  # 指數／外匯的成交量是 0，不顯示


def test_quote_extras_drops_nan_missing_and_inverted_ranges():
    nan = float("nan")
    fi = _FI(last_volume=nan, day_high=10.0, day_low=20.0, year_high=nan, year_low=1.0)
    out = yf_helpers.quote_extras(fi, 2)
    assert out["volume"] is None
    assert out["dayHigh"] is None and out["dayLow"] is None  # 高低顛倒＝資料有問題，整組丟掉
    assert out["yearHigh"] is None and out["yearLow"] == 1.0
    assert all(v is None for v in yf_helpers.quote_extras(object(), 2).values())  # 欄位全缺也不拋錯


def test_quote_extras_drops_split_artifact_year_range():
    """TOPIX ETF 1306.T 實測：一年低點 36.7、現價 435（拆股沒調整）——不顯示比顯示錯的好。"""
    fi = _FI(last_volume=5659030.0, day_high=435.4, day_low=433.4, year_high=439.9, year_low=36.7)
    out = yf_helpers.quote_extras(fi, 1)
    assert out["yearHigh"] is None and out["yearLow"] is None
    assert out["dayHigh"] == 435.4 and out["volume"] == 5659030  # 其他欄位照舊


# ── 名稱表與前端可選清單對得上（2026-10-05 實測找到的錯） ────────────────────────

_SYMBOL = re.compile(r"""['"]((?:\^[A-Z0-9]+)|(?:[A-Z0-9]{1,8}(?:\.[A-Z]{1,2}|=[FX])))['"]""")


def _table_symbols(router_file: str, table: str) -> set[str]:
    src = (ROOT / "api" / "routers" / router_file).read_text(encoding="utf-8")
    start = src.index(f"{table}: dict")
    return set(_SYMBOL.findall(src[start : src.index("\n}\n", start)]))


@pytest.mark.parametrize(
    "market,router_file,table,js_file",
    [
        ("jp", "jpstock.py", "_JP_STOCK_NAMES", "jpstock.js"),
        ("kr", "krstock.py", "_KR_STOCK_NAMES", "krstock.js"),
        ("cn", "astock.py", "_A_STOCK_NAMES", "astock.js"),
    ],
)
def test_every_frontend_symbol_has_a_name_entry(market, router_file, table, js_file):
    """前端可選的代號都要在後端名稱表裡——新聞搜尋詞（日韓文／中文名）靠它。"""
    backend = _table_symbols(router_file, table)
    js = (ROOT / "web" / "js" / js_file).read_text(encoding="utf-8")
    missing = sorted(set(_SYMBOL.findall(js)) - backend)
    assert not missing, f"{market}: 前端有、後端名稱表沒有：{missing}"


@pytest.mark.parametrize(
    "router_file,table",
    [("jpstock.py", "_JP_STOCK_NAMES"), ("krstock.py", "_KR_STOCK_NAMES")],
)
def test_jp_kr_entries_carry_a_local_language_name(router_file, table):
    src = (ROOT / "api" / "routers" / router_file).read_text(encoding="utf-8")
    start = src.index(f"{table}: dict")
    block = src[start : src.index("\n}\n", start)]
    entries = [ln for ln in block.splitlines() if re.match(r'\s+"[^"]+": \{', ln)]
    assert entries and all('"local":' in ln for ln in entries)


def test_known_dead_yahoo_symbols_are_gone():
    """2026-10-05 對 Yahoo 實測查無資料：TOPIX 指數沒有可用代號、NAVER／Kakao 在 KOSPI（.KS）、恒生銀行已查無資料。"""
    for path in [
        "api/routers/jpstock.py",
        "api/routers/krstock.py",
        "api/routers/hkstock.py",
        "web/js/jpstock.js",
        "web/js/krstock.js",
        "web/js/hkstock.js",
    ]:
        text = (ROOT / path).read_text(encoding="utf-8")
        for dead in ('"^TOPX"', "'^TOPX'", '"035420.KQ"', '"035720.KQ"', "0011.HK"):
            assert dead not in text, f"{path} 還有查無資料的代號 {dead}"
