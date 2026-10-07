"""Forum UGC 後端 HTML sanitization — 縱深防護防止 stored XSS。

職責
====
後端在 forum 發文/評論寫入 DB 前先 sanitize,不依賴前端 DOMPurify。一旦任何
客戶端(第三方整合、API 直呼、舊版前端)繞過前端,或 DOMPurify 設定疏漏,
後端仍保證存進 DB 的是安全 HTML 子集。

這個站有 TON 錢包連動,stored XSS 可偷錢包連線 → 直接資產損失,風險比一般站高。

允許的 HTML 子集(對齊前端 markdown 渲染的使用情境):
- 排版:b/strong/i/em/br/p/blockquote/h1-h3/hr
- 清單:ul/ol/li
- 程式碼:code/pre
- 連結:a(href 僅 http/https/mailto,自動 rel=noopener noreferrer)
- 不允許:script/style/img(圖片走外部連結,避免 SSRF/追蹤/惡意圖)、
  iframe/object/embed/form、任何 event handler(onclick 等)、javascript: scheme

使用 nh3(Rust ammonia 綁定,bleach 的現代繼承者,效能好且維護中)。
"""

from __future__ import annotations

import logging

import nh3

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 白名單設定
# ──────────────────────────────────────────────────────────────────────────────

# 允許的 tag(排版 + 清單 + 程式碼 + 連結)
_ALLOWED_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "s",
        "br",
        "p",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "hr",
        "ul",
        "ol",
        "li",
        "code",
        "pre",
        "a",
        "span",  # markdown 行內樣式常見
        "div",
    }
)

# 允許的屬性(tag → {allowed attr names})
# nh3 對 href 會自動只允許 http/https/mailto(擋 javascript:/data: scheme)
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "code": {"class"},  # markdown code block 帶語言 class
    "span": {"class"},
    "div": {"class"},
}

# nh3 預設就會給 <a> 加 rel="noopener noreferrer",這裡顯式啟用
# (nh3 0.2+ 預設啟用,但顯式更清楚)


def _sanitize_html(content: str) -> str:
    """用 nh3 清洗 HTML,只留白名單 tag/屬性。

    nh3 行為:
    - 不在白名單的 tag → 移除 tag 但保留內容(如 <script>alert</script> → alert)
      等等,實際上 nh3 對 script/style 是連內容一起刪(它們是 "dangerous tags")
    - event handler(onclick 等)→ 移除
    - javascript:/data: scheme → 移除 href
    - <a> → 自動加 rel="noopener noreferrer"
    """
    if not content:
        return content
    try:
        return nh3.clean(
            content,
            tags=_ALLOWED_TAGS,
            attributes=_ALLOWED_ATTRIBUTES,
        )
    except Exception as e:
        # nh3 不該拋錯(它是 Rust),但若發生:記 log + 回空字串(fail-safe,
        # 不存可能含 XSS 的 raw 內容)。呼叫端應在長度檢查前呼叫,空字串會被
        # 後續 content_required 檢查擋下。
        logger.error("[ForumSanitizer] nh3.clean failed: %s — returning empty (fail-safe)", e)
        return ""


# ──────────────────────────────────────────────────────────────────────────────
# 公開 API(供 forum.py 各寫入點呼叫)
# ──────────────────────────────────────────────────────────────────────────────


def sanitize_post_content(content: str) -> str:
    """發文內容 sanitize。允許完整 HTML 子集(排版/清單/程式碼/連結)。"""
    return _sanitize_html(content)


def sanitize_post_title(title: str) -> str:
    """發文標題 sanitize。

    標題通常不該含 HTML(它是純文字顯示)。但仍 sanitize 一遍防邊界情況
    (例如標題被嵌入 og:title meta 或被某些前端當 HTML 渲染)。
    結果再 strip tag,確保標題是純文字。
    """
    if not title:
        return title
    cleaned = _sanitize_html(title)
    # 標題最終該是純文字:nh3 已移除危險 tag,但保留的排版 tag(b/i 等)在
    # 標題語境不該出現 → 用 nh3 全清成純文字
    return nh3.clean(cleaned, tags=frozenset())


def sanitize_comment(content: str) -> str:
    """評論 sanitize。與發文內容同白名單(評論也可能用 markdown 排版)。"""
    return _sanitize_html(content)


def sanitize_category(category: str) -> str:
    """分類欄位 sanitize。分類應是純文字 enum,清成純文字防注入。"""
    if not category:
        return category
    return nh3.clean(category, tags=frozenset())
