"""Tests for XSS prevention utilities and patterns.

Verifies that frontend XSS protection patterns are correctly implemented.
"""

import os
import re

import pytest


def _read_js_file(filepath: str) -> str:
    full_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filepath)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read()


class TestSanitizeUrlPresence:
    """Verify sanitizeUrl() helper exists and blocks dangerous protocols."""

    SANITIZE_URL_FILE = "web/js/utils.js"

    def test_has_sanitize_url_function(self):
        content = _read_js_file(self.SANITIZE_URL_FILE)
        assert "sanitizeUrl" in content, (
            f"{self.SANITIZE_URL_FILE} missing sanitizeUrl helper"
        )

    def test_sanitize_url_blocks_javascript_protocol(self):
        content = _read_js_file(self.SANITIZE_URL_FILE)
        match = re.search(r"sanitizeUrl\s*\([^)]*\)\s*\{([^}]+)\}", content, re.DOTALL)
        assert match is not None, (
            f"{self.SANITIZE_URL_FILE}: sanitizeUrl function not found"
        )
        body = match.group(1)
        assert "javascript:" in body.lower(), (
            f"{self.SANITIZE_URL_FILE}: sanitizeUrl does not check for javascript: protocol"
        )

    def test_sanitize_url_blocks_data_protocol(self):
        content = _read_js_file(self.SANITIZE_URL_FILE)
        match = re.search(r"sanitizeUrl\s*\([^)]*\)\s*\{([^}]+)\}", content, re.DOTALL)
        assert match is not None, (
            f"{self.SANITIZE_URL_FILE}: sanitizeUrl function not found"
        )
        body = match.group(1)
        assert "data:" in body.lower(), (
            f"{self.SANITIZE_URL_FILE}: sanitizeUrl does not check for data: protocol"
        )

    @pytest.mark.parametrize(
        "filepath",
        [
            "web/js/usstock.js",
            "web/js/twstock.js",
            "web/js/astock.js",
            "web/js/hkstock.js",
            "web/js/instock.js",
            "web/js/jpstock.js",
            "web/js/krstock.js",
            "web/js/pulse.js",
        ],
    )
    def test_files_import_sanitize_url(self, filepath):
        content = _read_js_file(filepath)
        assert "sanitizeUrl" in content, f"{filepath} missing sanitizeUrl reference"


class TestEscapeHtmlPresence:
    """Verify escapeHtml() is used in files that render API data."""

    @pytest.mark.parametrize(
        "filepath",
        [
            "web/js/forex.js",
            "web/js/commodity.js",
            "web/js/usstock.js",
            "web/js/twstock.js",
            "web/js/messages.js",
            "web/js/astock.js",
            "web/js/hkstock.js",
            "web/js/instock.js",
            "web/js/jpstock.js",
            "web/js/krstock.js",
            "web/js/pulse.js",
        ],
    )
    def test_uses_escape_html(self, filepath):
        content = _read_js_file(filepath)
        assert "escapeHtml" in content, f"{filepath} does not use escapeHtml"


class TestNoUnescapedHrefInJs:
    """Verify that href attributes use sanitized URLs, not raw API data."""

    @pytest.mark.parametrize(
        "filepath",
        [
            "web/js/forex.js",
            "web/js/commodity.js",
            "web/js/usstock.js",
            "web/js/twstock.js",
            "web/js/astock.js",
            "web/js/hkstock.js",
            "web/js/instock.js",
            "web/js/jpstock.js",
            "web/js/krstock.js",
            "web/js/pulse.js",
        ],
    )
    def test_href_uses_sanitize_url(self, filepath):
        content = _read_js_file(filepath)
        # Find all href="${...}" patterns that use API data variables
        # (not hardcoded URLs like '/static/...')
        href_patterns = re.findall(r'href="\$\{([^}]+)\}"', content)
        for pattern in href_patterns:
            expr = pattern.strip()
            # Check if the expression starts with sanitizeUrl
            is_safe = expr.startswith("sanitizeUrl(")
            if not is_safe:
                # Also check for direct property access like sanitizeUrl(item.url)
                prop_match = re.search(
                    rf"sanitizeUrl\(\s*{re.escape(expr)}\s*\)", content
                )
                msg = f"{filepath}: href=... uses raw variable without sanitizeUrl(): {expr}"
                assert prop_match is not None, msg


class TestAIContentEscaping:
    """Verify AI-generated content (summary, key_points) is escaped."""

    @pytest.mark.parametrize(
        "filepath,field",
        [
            ("web/js/forex.js", "summary"),
            ("web/js/commodity.js", "summary"),
            ("web/js/usstock.js", "summary"),
            ("web/js/twstock.js", "summary"),
        ],
    )
    def test_ai_summary_escaped(self, filepath, field):
        content = _read_js_file(filepath)
        # Find patterns like: ${d.report?.summary || ''}
        # The summary should be wrapped in escapeHtml()
        summary_patterns = re.findall(rf"\$\{{[^}}]*{field}[^}}]*\}}", content)
        for pattern in summary_patterns:
            # Check if this specific pattern uses escapeHtml
            escaped = "escapeHtml(" in content
            assert escaped, f"{filepath}: {field} is rendered without escapeHtml"


class TestErrorMessagesEscaped:
    """Verify error messages displayed via innerHTML are escaped."""

    @pytest.mark.parametrize(
        "filepath",
        [
            "web/js/forex.js",
            "web/js/commodity.js",
        ],
    )
    def test_error_message_escaped(self, filepath):
        content = _read_js_file(filepath)
        # Find: ${e.message} in innerHTML context
        error_patterns = re.findall(r"\$\{{e\.message[^}]*\}}", content)
        if error_patterns:
            assert "escapeHtml" in content, (
                f"{filepath}: error messages (e.message) not escaped"
            )


class TestNewsCardFieldsEscaped:
    """新聞卡片的每一個第三方欄位都必須包 escapeHtml/sanitizeUrl。

    2026-08-24 runtime 驗證抓到：前一輪只修了 item.url 與 item.title，
    同一個 template 裡的 item.publisher / item.pub_str 仍是裸插值——
    餵 `<img src=x onerror=...>` 進去 onerror 真的會執行。
    舊守衛（TestEscapeHtmlPresence）只檢查「檔案裡有沒有出現 escapeHtml」，
    一個欄位有包就整檔算過，所以抓不到。這裡改成逐欄位檢查。
    """

    NEWS_FILES = [
        "web/js/astock.js",
        "web/js/commodity.js",
        "web/js/forex.js",
        "web/js/hkstock.js",
        "web/js/instock.js",
        "web/js/jpstock.js",
        "web/js/krstock.js",
        "web/js/pulse.js",
        "web/js/usstock.js",
        "web/js/twstock.js",
    ]

    # 第三方新聞來源的欄位——全部經由 /news 端點，後端不做 HTML 消毒
    NEWS_FIELDS = ("title", "publisher", "pub_str", "url", "summary", "source")

    @pytest.mark.parametrize("filepath", NEWS_FILES)
    def test_news_fields_are_escaped(self, filepath):
        content = _read_js_file(filepath)
        # 裸插值：${item.title} / ${n.publisher} —— 中間沒有任何函式呼叫
        raw = re.findall(
            r"\$\{\s*(?:item|n|news)\.(" + "|".join(self.NEWS_FIELDS) + r")\s*\}",
            content,
        )
        assert not raw, (
            f"{filepath}：新聞欄位未經 escapeHtml/sanitizeUrl 直接插進 HTML —— "
            f"{sorted(set(raw))}"
        )


class TestEscapeHtmlQuotes:
    """SecurityUtils.escapeHTML 也被用在屬性值（data-username="..."），引號一定要轉。

    2026-09-25 前它用 textContent→innerHTML，只轉 <>&；正式站 CSP 的 script-src
    有 'unsafe-inline'，inline handler 會執行，所以跳出屬性就是 XSS。
    """

    def test_escapes_tags_and_quotes(self, tmp_path):
        import json
        import shutil
        import subprocess

        if shutil.which("node") is None:
            pytest.skip("node not installed")
        src = _read_js_file("web/js/security-utils.js")
        mod = tmp_path / "security-utils.mjs"
        mod.write_text(src, encoding="utf-8")
        runner = tmp_path / "run.mjs"
        runner.write_text(
            "globalThis.window = {};\n"
            "globalThis.document = { readyState: 'complete', addEventListener() {} };\n"
            "const { SecurityUtils } = await import('./security-utils.mjs');\n"
            "const cases = [`<img src=x onerror=\"a('1')\">`, 0, '', null, 'R&D'];\n"
            "console.log(JSON.stringify(cases.map((c) => SecurityUtils.escapeHTML(c))));\n",
            encoding="utf-8",
        )
        out = subprocess.run(
            ["node", str(runner)], capture_output=True, text=True, timeout=30, check=True
        ).stdout.strip()
        assert json.loads(out) == [
            "&lt;img src=x onerror=&quot;a(&#039;1&#039;)&quot;&gt;",
            "0",
            "",
            "",
            "R&amp;D",
        ]


class TestUserControlledFieldsEscaped:
    """別人能控制的字（使用者名稱、私訊內容、通知、對話標題、外部新聞）進 innerHTML 前要轉義。"""

    @pytest.mark.parametrize(
        "filepath, raw",
        [
            ("web/js/friends.js", "${user.username || user.user_id}</span>"),
            ("web/js/friends.js", "truncate pr-2\">${username}</h4>"),
            ("web/js/friends.js", "opacity-80\">${lastMessage}</p>"),
            ("web/js/components/NotificationPanel.js", "${notification.title}</div>"),
            ("web/js/components/NotificationPanel.js", "${notification.body}</div>"),
            ("web/js/components/NotificationPanel.js", "${notification.title || ''}</h3>"),
            ("web/js/components/NotificationPanel.js", "${notification.body || ''}</p>"),
            ("web/js/chat-sessions.js", "${session.title || 'New Chat'}</div>"),
            ("web/js/pulse.js", "${news.title || t('pulse.noTitle')}</h4>"),
            ("web/js/toolSettings.js", "`✗ ${reason}${latency}`"),
            ("web/js/chat-hitl.js", "mb-2\">${question}</p>"),
            # 空狀態訊息帶標籤名稱（別人發文時自訂的），i18next 的 escapeValue 是 false
            ("web/js/forum-app.js", "mx-auto\">${this.getFilteredEmptyStateMessage()}</p>"),
        ],
    )
    def test_no_raw_interpolation(self, filepath, raw):
        assert raw not in _read_js_file(filepath), f"{filepath} 仍直接插入未轉義的 {raw}"
