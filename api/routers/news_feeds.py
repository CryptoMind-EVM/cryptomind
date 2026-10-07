"""免費、免金鑰的新聞／公告來源（日／韓／A／港股、商品、外匯的新聞區用）。

Yahoo 對日、韓、A 股的代號幾乎沒有新聞（2026-10-05 實測：AAPL、黃金各 5 則，
7203.T／005930.KS／600519.SS 都是 0 則），這幾個市場的新聞區因此一直是空的。
補三個來源，全部免費、不需要金鑰：

- Google News RSS：各市場當地語言的媒體報導（每則附媒體名稱）
- TDnet 適時開示：日股公司的官方公告（yanoshin.jp 免費轉發 API）
- 東方財富公告：A 股公司的官方公告

都不是官方保證的 API，格式可能變動：任何失敗一律回空清單，不影響 Yahoo 與其他來源。
每則新聞的 ``publisher`` 是來源名稱，``feed`` 標明是哪一條管道（前端不用另外猜）。
"""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import httpx

from api.routers._ttl_cache import TTLCache
from api.utils import logger

_TIMEOUT = 6.0
_MAX_BYTES = 2_000_000  # RSS／JSON 超過這個大小就不收（防被餵超大內容）
_UA = {"User-Agent": "Mozilla/5.0 (compatible; CryptoMind/1.0)"}
_cache = TTLCache(default_ttl=600)

# Google News 各市場的版本（hl, gl, ceid）。日／韓／A／港股用當地版本——當地媒體的報導才查得到。
_GOOGLE_LOCALES = {
    "jp": ("ja", "JP", "JP:ja"),
    "kr": ("ko", "KR", "KR:ko"),
    "cn": ("zh-CN", "CN", "CN:zh-Hans"),
    "hk": ("zh-HK", "HK", "HK:zh-Hant"),
}
# 商品、外匯依使用者語言（en 用美國版）
_GOOGLE_LOCALES_BY_LANG = {
    "en": ("en-US", "US", "US:en"),
    "zh-TW": ("zh-TW", "TW", "TW:zh-Hant"),
}

# 指數／商品／外匯沒有「公司名」，直接寫搜尋詞：代號 → {語言: 搜尋詞}（找不到語言用 zh-TW）
_TOPIC_QUERIES: dict[str, dict[str, str]] = {
    "^N225": {"local": "日経平均 株価", "en": "Nikkei 225"},
    "1306.T": {"local": "TOPIX 東証株価指数", "en": "TOPIX"},
    "^KS11": {"local": "코스피", "en": "KOSPI"},
    "^KQ11": {"local": "코스닥", "en": "KOSDAQ"},
    "000001.SS": {"local": "上证指数", "en": "Shanghai Composite"},
    "399001.SZ": {"local": "深证成指", "en": "Shenzhen Component"},
    "399006.SZ": {"local": "创业板指", "en": "ChiNext"},
    "^HSI": {"local": "恒指 恒生指數", "en": "Hang Seng Index"},
    "^HSCE": {"local": "國企指數 恒生中國企業", "en": "Hang Seng China Enterprises"},
    "GC=F": {"zh-TW": "黃金 金價 國際", "en": "gold price"},
    "SI=F": {"zh-TW": "白銀 銀價", "en": "silver price"},
    "PL=F": {"zh-TW": "鉑金 白金 價格", "en": "platinum price"},
    "PA=F": {"zh-TW": "鈀金 價格", "en": "palladium price"},
    "CL=F": {"zh-TW": "WTI 原油 油價", "en": "WTI crude oil price"},
    "BZ=F": {"zh-TW": "布蘭特原油 油價", "en": "Brent crude oil price"},
    "NG=F": {"zh-TW": "天然氣 價格 期貨", "en": "natural gas price"},
    "HG=F": {"zh-TW": "銅價 期貨", "en": "copper price"},
    "ALI=F": {"zh-TW": "鋁價 期貨", "en": "aluminum price"},
    "ZW=F": {"zh-TW": "小麥 期貨 價格", "en": "wheat futures"},
    "ZC=F": {"zh-TW": "玉米 期貨 價格", "en": "corn futures"},
    "ZS=F": {"zh-TW": "黃豆 期貨 價格", "en": "soybean futures"},
    "KC=F": {"zh-TW": "咖啡 期貨 價格", "en": "coffee futures"},
    "SB=F": {"zh-TW": "糖 期貨 價格", "en": "sugar futures"},
    "CT=F": {"zh-TW": "棉花 期貨 價格", "en": "cotton futures"},
}

# 搜尋結果裡的報價頁／討論板／預測文：不是新聞，濾掉（2026-10-05 實測 Yahoo!ファイナンス、
# Moomoo 佔了日股查詢的前幾名）
_NOISE_PUBLISHERS = {
    "Yahoo!ファイナンス",
    "Moomoo",
    "Traders Union",
    "LiteFinance",
    "MarketScreener",
    "Bybit",  # 外匯查詢會混進「將 10 EUR 兌換為 ICX」這類幣種換算頁
}
_NOISE_TITLE_HINTS = (
    "掲示板",
    "株主優待",
    "株価リアルタイム",
    "股價、新聞、報價",
    "価格予想",
    "주가 전망",
    "兌換為",
)


def _lang_key(lang: str) -> str:
    """商品／外匯新聞的語言：中文（繁簡）用台灣版，其餘（en、ru…）用美國英文版。"""
    return "zh-TW" if (lang or "zh-TW").lower().startswith("zh") else "en"


def _query_for(
    market: str, symbol: str, names: Optional[dict], lang: str
) -> Optional[str]:
    """這個代號要搜什麼：指數／商品／外匯用 _TOPIC_QUERIES；個股用「當地名稱＋代號」。"""
    topic = _TOPIC_QUERIES.get(symbol)
    if topic:
        if market in ("commodity", "forex"):
            key = _lang_key(lang)
            return topic.get(key) or topic.get("zh-TW")
        return topic.get("local")
    code = symbol.split(".")[0]
    entry = (names or {}).get(symbol) or {}
    if market in ("jp", "kr"):
        name = entry.get("local") or entry.get("en")
        return f"{name} {code}" if name else code
    if market in ("cn", "hk"):
        name = entry.get("zh") or entry.get("en")
        return f"{name} {code}" if name else code
    if market == "forex" and len(symbol) >= 8 and symbol.endswith("=X"):
        base, quote = symbol[:3], symbol[3:6]  # EURUSD=X → EUR／USD
        return (
            f"{base}/{quote} exchange rate"
            if _lang_key(lang) == "en"
            else f"{base} {quote} 匯率"
        )
    return None


def _is_noise(title: str, publisher: str) -> bool:
    return publisher in _NOISE_PUBLISHERS or any(h in title for h in _NOISE_TITLE_HINTS)


def _item(
    symbol: str, title: str, url: str, publisher: str, ts: int, feed: str
) -> dict:
    pub_str = (
        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        if ts
        else ""
    )
    return {
        "symbol": symbol,
        "title": title,
        "url": url,
        "publisher": publisher,
        "published": ts,
        "pub_str": pub_str,
        "feed": feed,
    }


async def _get(url: str, **params) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_UA, follow_redirects=True
        ) as client:
            resp = await client.get(url, params=params or None)
            resp.raise_for_status()
            return resp.content if len(resp.content) <= _MAX_BYTES else None
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.info("[news_feeds] %s failed: %s", url.split("?")[0], type(exc).__name__)
        return None


def parse_google_news(xml_bytes: bytes, symbol: str, limit: int) -> list[dict]:
    """Google News RSS → 新聞清單（純函式，方便測）。"""
    # stdlib 的 XML 解析不收 DTD／實體宣告：RSS 本來就不會有，有的一律當壞資料
    if not xml_bytes or b"<!DOCTYPE" in xml_bytes or b"<!ENTITY" in xml_bytes:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items: list[dict] = []
    for node in root.findall("./channel/item"):
        title = (node.findtext("title") or "").strip()
        publisher = (node.findtext("source") or "").strip()
        url = (node.findtext("link") or "").strip()
        if not title or not url.startswith("http") or _is_noise(title, publisher):
            continue
        if publisher and title.endswith(f" - {publisher}"):
            title = title[
                : -len(publisher) - 3
            ].rstrip()  # Google 會把「 - 媒體名」接在標題後面
        try:
            ts = int(parsedate_to_datetime(node.findtext("pubDate") or "").timestamp())
        except (TypeError, ValueError):
            ts = 0
        items.append(
            _item(symbol, title, url, publisher or "Google News", ts, "google_news")
        )
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:limit]


async def google_news(
    market: str, symbol: str, names: Optional[dict], lang: str, limit: int
) -> list[dict]:
    locale = _GOOGLE_LOCALES.get(market) or _GOOGLE_LOCALES_BY_LANG[_lang_key(lang)]
    query = _query_for(market, symbol, names, lang)
    if not query:
        return []
    key = f"gn:{locale[1]}:{query}"
    cached = _cache.get(key)
    if cached is not None:
        return [{**i, "symbol": symbol} for i in cached][:limit]
    body = await _get(
        "https://news.google.com/rss/search",
        q=f"{query} when:14d",
        hl=locale[0],
        gl=locale[1],
        ceid=locale[2],
    )
    items = parse_google_news(body or b"", symbol, 20)
    if items:
        _cache.set(key, items)
    return items[:limit]


_JST = timezone(timedelta(hours=9))
_TDNET_REDIRECT = "https://webapi.yanoshin.jp/rd.php?"


def parse_tdnet(payload: bytes, symbol: str, limit: int) -> list[dict]:
    """yanoshin TDnet JSON → 公告清單。連結拆掉轉址前綴，直接指向 TDnet 的公告 PDF。"""
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return []
    items: list[dict] = []
    for row in (data.get("items") or [])[:limit]:
        doc = (row or {}).get("Tdnet") or {}
        title = (doc.get("title") or "").strip()
        url = (doc.get("document_url") or "").strip()
        if url.startswith(_TDNET_REDIRECT):
            url = url[len(_TDNET_REDIRECT) :]
        if not title or not url.startswith("http"):
            continue
        try:
            ts = int(
                datetime.strptime(doc.get("pubdate", ""), "%Y-%m-%d %H:%M:%S")
                .replace(tzinfo=_JST)
                .timestamp()
            )
        except ValueError:
            ts = 0
        items.append(_item(symbol, title, url, "TDnet 適時開示", ts, "tdnet"))
    return items


async def tdnet_disclosures(symbol: str, limit: int) -> list[dict]:
    code = symbol.removesuffix(".T")
    if not code.isalnum():
        return []
    key = f"tdnet:{code}"
    cached = _cache.get(key)
    if cached is not None:
        return cached[:limit]
    body = await _get(
        f"https://webapi.yanoshin.jp/webapi/tdnet/list/{code}.json", limit=10
    )
    items = parse_tdnet(body or b"", symbol, 10)
    if items:
        _cache.set(key, items)
    return items[:limit]


def parse_eastmoney(payload: bytes, symbol: str, limit: int) -> list[dict]:
    """東方財富公告 JSON（可能包在 JSONP 括號裡）→ 公告清單。"""
    text = (payload or b"").decode("utf-8", errors="replace")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(text[start : end + 1])
        rows = (data.get("data") or {}).get("list") or []
    except (ValueError, AttributeError):
        return []
    code = symbol.split(".")[0]
    items: list[dict] = []
    for row in rows[:limit]:
        title = (row.get("title") or "").strip()
        art = (row.get("art_code") or "").strip()
        if not title or not art.isalnum():
            continue
        try:
            ts = int(
                datetime.strptime((row.get("notice_date") or "")[:10], "%Y-%m-%d")
                .replace(tzinfo=timezone(timedelta(hours=8)))
                .timestamp()
            )
        except ValueError:
            ts = 0
        items.append(
            _item(
                symbol,
                title,
                f"https://data.eastmoney.com/notices/detail/{code}/{art}.html",
                "东方财富公告",
                ts,
                "eastmoney",
            )
        )
    return items


async def eastmoney_announcements(symbol: str, limit: int) -> list[dict]:
    code = symbol.split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return []
    key = f"em:{code}"
    cached = _cache.get(key)
    if cached is not None:
        return cached[:limit]
    body = await _get(
        "https://np-anotice-stock.eastmoney.com/api/security/ann",
        sr=-1,
        page_size=10,
        page_index=1,
        ann_type="A",
        client_source="web",
        stock_list=code,
        f_node=0,
        s_node=0,
    )
    items = parse_eastmoney(body or b"", symbol, 10)
    if items:
        _cache.set(key, items)
    return items[:limit]


async def fetch_feed_news(
    market: str,
    symbol: str,
    names: Optional[dict] = None,
    lang: str = "zh-TW",
    limit: int = 5,
) -> list[dict]:
    """單一代號的補充新聞：官方公告（有的市場）＋ Google News。任何來源失敗都當空。"""
    jobs = [google_news(market, symbol, names, lang, limit)]
    if market == "jp" and not symbol.startswith("^"):
        jobs.append(tdnet_disclosures(symbol, limit))
    if market == "cn" and symbol.endswith((".SS", ".SZ")):
        jobs.append(eastmoney_announcements(symbol, limit))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    merged: list[dict] = []
    for res in results:
        if isinstance(res, BaseException):
            if isinstance(res, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise res
            logger.info("[news_feeds] %s %s: %s", market, symbol, type(res).__name__)
            continue
        merged.extend(res)
    return merged
