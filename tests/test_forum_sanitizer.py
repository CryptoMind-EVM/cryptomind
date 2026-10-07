"""Forum UGC sanitizer 測試 — core/validators/forum_sanitizer.py

依 AGENTS.md「success / reject / edge」慣例:
- success:合法 HTML 子集保留(排版/連結/程式碼)
- reject(防 XSS):script/img-onerror/javascript:/iframe/svg-onload/event-handler 全擋
- edge:空字串、純文字、巢狀攻擊

背景:這個站有 TON 錢包連動,stored XSS 可偷錢包連線 → 資產損失,風險特別高。
後端在寫入 DB 前 sanitize,不依賴前端 DOMPurify(縱深防護)。
"""

from __future__ import annotations

import pytest

from core.validators.forum_sanitizer import (
    sanitize_category,
    sanitize_comment,
    sanitize_post_content,
    sanitize_post_title,
)

# ──────────────────────────────────────────────────────────────────────────────
# Success — 合法 HTML 子集保留
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_allowed_formatting_tags_preserved():
    """排版 tag(b/strong/i/em)該保留。"""
    out = sanitize_post_content("<b>粗體</b><strong>強調</strong><i>斜</i>")
    assert "<b>粗體</b>" in out
    assert "<strong>強調</strong>" in out
    assert "<i>斜</i>" in out


@pytest.mark.unit
def test_allowed_list_tags_preserved():
    """清單 tag(ul/ol/li)該保留。"""
    out = sanitize_post_content("<ul><li>項目一</li><li>項目二</li></ul>")
    assert "<ul>" in out
    assert "<li>項目一</li>" in out


@pytest.mark.unit
def test_code_block_preserved():
    """程式碼 tag(code/pre)該保留。"""
    out = sanitize_post_content("<pre><code>print('hi')</code></pre>")
    assert "<pre>" in out
    assert "<code>" in out


@pytest.mark.unit
def test_safe_link_preserved_with_rel():
    """安全連結保留,且自動加 rel=noopener noreferrer。"""
    out = sanitize_post_content('<a href="https://example.com">連結</a>')
    assert 'href="https://example.com"' in out
    assert "rel=" in out
    assert "noopener" in out
    assert "noreferrer" in out


@pytest.mark.unit
def test_blockquote_preserved():
    out = sanitize_post_content("<blockquote>引用文字</blockquote>")
    assert "<blockquote>" in out


@pytest.mark.unit
def test_plain_text_preserved():
    """純文字不該被破壞。"""
    text = "這是一段正常的分析文字,BTC 現價 64000。"
    assert sanitize_post_content(text) == text


# ──────────────────────────────────────────────────────────────────────────────
# Reject — XSS 攻擊 payload 全擋
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_script_tag_removed():
    """<script> tag 連內容一起移除(dangerous tag)。"""
    out = sanitize_post_content("<script>alert('xss')</script>正常文字")
    assert "<script" not in out
    assert "alert" not in out
    assert "正常文字" in out


@pytest.mark.unit
def test_img_onerror_removed():
    """<img onerror> 整個移除(img 不在白名單)。"""
    out = sanitize_post_content("<img src=x onerror=alert(1)>")
    assert "<img" not in out
    assert "onerror" not in out


@pytest.mark.unit
def test_javascript_scheme_removed():
    """javascript: scheme 的 href 移除(link 變無 href)。"""
    out = sanitize_post_content('<a href="javascript:alert(1)">click</a>')
    assert "javascript:" not in out
    assert "alert" not in out


@pytest.mark.unit
def test_iframe_removed():
    out = sanitize_post_content("<iframe src='https://evil.com'></iframe>")
    assert "<iframe" not in out
    assert "evil.com" not in out


@pytest.mark.unit
def test_svg_onload_removed():
    """<svg onload> 移除(svg 不在白名單)。"""
    out = sanitize_post_content("<svg/onload=alert(1)>")
    assert "<svg" not in out
    assert "onload" not in out
    assert "alert" not in out


@pytest.mark.unit
def test_event_handler_removed():
    """event handler(onclick/onload 等)移除,tag 保留。"""
    out = sanitize_post_content('<div onclick="alert(1)">內容</div>')
    assert "onclick" not in out
    assert "alert" not in out
    assert "內容" in out  # tag 內容保留


@pytest.mark.unit
def test_link_with_event_handler_sanitized():
    """<a> 帶 onclick:onclick 移除,安全 href 保留。"""
    out = sanitize_post_content('<a href="https://safe.com" onclick="evil()">l</a>')
    assert "onclick" not in out
    assert "evil" not in out
    assert 'href="https://safe.com"' in out


@pytest.mark.unit
def test_nested_script_in_allowed_tag():
    """合法 tag 內藏 script 也該被清。"""
    out = sanitize_post_content("<b><script>alert(1)</script>粗體</b>")
    assert "<script" not in out
    assert "alert" not in out
    assert "粗體" in out


@pytest.mark.unit
def test_data_uri_img_removed():
    """data: URI 的 img 移除。"""
    out = sanitize_post_content('<img src="data:image/svg+xml,<svg onload=alert(1)>">')
    assert "<img" not in out
    assert "onload" not in out


# ──────────────────────────────────────────────────────────────────────────────
# Title / Comment / Category
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_title_stripped_to_plain_text():
    """標題該是純文字(連排版 tag 都清掉)。"""
    out = sanitize_post_title("<b>標題</b><script>x</script>")
    assert "<" not in out  # 無任何 tag
    assert ">" not in out
    assert "標題" in out


@pytest.mark.unit
def test_title_plain_text_passthrough():
    assert sanitize_post_title("正常的純標題") == "正常的純標題"


@pytest.mark.unit
def test_comment_same_as_content():
    """評論用與發文相同的白名單。"""
    out = sanitize_comment("<b>推</b><script>alert(1)</script>")
    assert "<b>推</b>" in out
    assert "<script" not in out


@pytest.mark.unit
def test_category_plain_text():
    """分類該是純文字。"""
    out = sanitize_category("analysis<script>alert(1)</script>")
    assert "<" not in out
    assert "analysis" in out


# ──────────────────────────────────────────────────────────────────────────────
# Edge
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_empty_string():
    assert sanitize_post_content("") == ""
    assert sanitize_post_title("") == ""
    assert sanitize_comment("") == ""
    assert sanitize_category("") == ""


@pytest.mark.unit
def test_only_script_returns_empty_or_safe():
    """純 script tag(無其他內容)→ script 連內容刪,剩空或安全殘留。"""
    out = sanitize_post_content("<script>alert(1)</script>")
    assert "alert" not in out
    assert "<script" not in out


@pytest.mark.unit
def test_mixed_legit_and_attack():
    """合法內容夾雜攻擊 payload:合法保留、攻擊移除。"""
    text = (
        "<b>BTC 分析</b>:技術面偏多。"
        "<script>steal(document.cookie)</script>"
        '<a href="https://chart.example.com">圖表</a>'
        "<img src=x onerror=alert(1)>"
    )
    out = sanitize_post_content(text)
    assert "<b>BTC 分析</b>" in out  # 合法保留
    assert "圖表" in out  # 連結保留
    assert "steal" not in out  # script 移除
    assert "<script" not in out
    assert "<img" not in out  # img 移除
    assert "onerror" not in out
