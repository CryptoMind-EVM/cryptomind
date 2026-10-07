"""get_crypto_news_google 的兩個根因修復（2026-09-14）：

1. ticker 撞名過濾：Google News 對「ENS + crypto」這類關鍵字查詢會混入
   同代號的非加密公司（EnerSys NYSE:ENS），模型曾把電池公司新聞當成
   Ethereum Name Service 的消息。
2. 日期語意：Google News RSS 的 pubDate 是「條目進 feed 的時間」，
   轉載／重新收錄會蓋掉原文日期（4 月的 eth.limo 事件帶 9 月時間戳），
   不可當原文發表時間引用——每筆結果必須帶 date_semantics 標注。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from utils.utils import get_crypto_news_google

pytestmark = pytest.mark.unit


def _rss_item(title: str, description: str, pub_date: str = "Wed, 09 Sep 2026 08:00:00 GMT") -> str:
    return (
        "<item>"
        f"<title>{title}</title>"
        "<link>https://example.com/x</link>"
        "<source>EX</source>"
        f"<description>{description}</description>"
        f"<pubDate>{pub_date}</pubDate>"
        "</item>"
    )


def _rss(*items: str) -> bytes:
    return (
        "<?xml version='1.0'?><rss><channel>"
        + "".join(items)
        + "</channel></rss>"
    ).encode("utf-8")


def _fetch(rss: bytes):
    response = MagicMock()
    response.content = rss
    response.raise_for_status = MagicMock()
    with patch("utils.utils.requests.get", return_value=response):
        return get_crypto_news_google("ENS", limit=10)


def test_ticker_collision_with_non_crypto_company_is_filtered():
    """EnerSys（NYSE: ENS）是電池公司——標題內容無加密訊號必須被過濾。"""
    rss = _rss(
        _rss_item(
            "EnerSys board declares quarterly dividend",
            "EnerSys Corporation announced battery energy systems results.",
        ),
        _rss_item(
            "Ethereum Name Service (ENS) price drops amid market sell-off",
            "The ENS token fell as Ethereum names trading volume declined.",
        ),
    )
    news = _fetch(rss)
    titles = [n["title"] for n in news]
    assert titles == [
        "Ethereum Name Service (ENS) price drops amid market sell-off"
    ]


def test_crypto_signal_keywords_keep_genuine_items():
    """不同措辭的加密新聞都要能通過過濾（bitcoin / .eth / name service）。"""
    rss = _rss(
        _rss_item("Bitcoin ETF inflows hit weekly record", "Spot ETF demand rose."),
        _rss_item(
            "ENS: .eth domain registrations reach new record",
            "Name service registrations climbed.",
        ),
        _rss_item(
            "DeFi protocol volumes recover as token markets stabilize",
            "Onchain activity improved.",
        ),
    )
    news = _fetch(rss)
    assert len(news) == 3


def test_every_item_carries_feed_ingestion_time_semantics():
    """每筆結果必須標注 pubDate 的語意，防止模型把收錄時間當原文發表日。"""
    rss = _rss(
        _rss_item("Bitcoin ETF inflows hit weekly record", "Spot ETF demand rose.")
    )
    news = _fetch(rss)
    assert news
    for item in news:
        assert item["date_semantics"] == "feed_ingestion_time"
        assert item["published_at"]  # 原始 pubDate 仍保留（本例為 9/9 收錄時間）


def test_missing_pub_date_still_returns_item():
    """沒有 pubDate 的條目不該讓整批新聞消失（邊界）。"""
    rss_bytes = (
        "<?xml version='1.0'?><rss><channel>"
        "<item><title>Bitcoin fee market shifts</title>"
        "<link>https://example.com/y</link>"
        "<description>Onchain fees changed.</description></item>"
        "</channel></rss>"
    ).encode("utf-8")
    news = _fetch(rss_bytes)
    assert len(news) == 1
    assert news[0]["published_at"] == "N/A"
    assert news[0]["date_semantics"] == "feed_ingestion_time"
