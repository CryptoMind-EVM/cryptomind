"""`/?ask=` 分享連結的預覽 meta：換成問題、跳脫 HTML、加 noindex。"""

import re

import pytest

pytest.importorskip("fastapi")

from api.routers.system import apply_ask_share_meta  # noqa: E402

PAGE = (
    "<head>"
    '<meta property="og:title" content="CryptoMind - AI Crypto Analyst">'
    '<meta property="og:description" content="default">'
    '<meta name="twitter:title" content="CryptoMind - AI Crypto Analyst">'
    '<meta name="twitter:description" content="default">'
    "</head>"
)


def test_no_ask_leaves_page_unchanged():
    assert apply_ask_share_meta(PAGE, None) == PAGE
    assert apply_ask_share_meta(PAGE, "   ") == PAGE


def test_ask_replaces_title_and_adds_noindex():
    out = apply_ask_share_meta(PAGE, "BTC 現在怎麼看？")
    assert "BTC 現在怎麼看？" in re.search(r'og:title" content="([^"]*)"', out).group(1)
    assert "BTC 現在怎麼看？" in re.search(
        r'twitter:title" content="([^"]*)"', out
    ).group(1)
    assert "default" not in out
    assert 'name="robots" content="noindex"' in out


def test_ask_is_html_escaped():
    out = apply_ask_share_meta(PAGE, '"><script>alert(1)</script>')
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_ask_is_truncated():
    out = apply_ask_share_meta(PAGE, "a" * 500)
    title = re.search(r'og:title" content="([^"]*)"', out).group(1)
    assert title.count("a") == 200


def test_existing_robots_meta_is_replaced_not_duplicated():
    page = PAGE.replace("<head>", '<head><meta name="robots" content="index, follow">')
    out = apply_ask_share_meta(page, "BTC")
    assert out.count('name="robots"') == 1
    assert 'name="robots" content="noindex"' in out
    assert "index, follow" not in out
