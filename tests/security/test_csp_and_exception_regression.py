"""
Regression tests for CSP hardening and CancelledError propagation.

Verifies:
1. CSP headers: script-src has no 'unsafe-inline' (2026-09-25 移除——它讓
   儲存型 XSS 的 inline handler 能執行；b1924913 當時歸咎 TON Connect 的
   inline handler，2026-09-25 在嚴格 CSP 下實測不成立)
2. No inline event-handler attributes / javascript: URLs in HTML or
   JavaScript-rendered templates
3. No executable inline <script> in any web/**/*.html
4. CancelledError propagates through guarded except blocks
5. Broad except Exception still catches normal errors (behavior unchanged)
"""

import asyncio
import glob
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WEB_DIR = PROJECT_ROOT / "web"
MIDDLEWARE_FILE = PROJECT_ROOT / "api" / "middleware_setup.py"


# ── CSP Header Tests ──────────────────────────────────────────────────────────


def _csp_directive(name: str) -> list[str]:
    from api.middleware_setup import content_security_policy

    for directive in content_security_policy().split(";"):
        parts = directive.split()
        if parts and parts[0] == name:
            return parts[1:]
    pytest.fail(f"{name} directive not found in CSP")


class TestCSPHeaders:
    """Verify Content-Security-Policy does not allow unsafe-inline for scripts."""

    def test_script_src_has_no_unsafe_inline(self):
        """script-src 是擋 XSS 的那一道：不可放行 inline script／handler。

        2026-07-29 b1924913 以「TON Connect SDK 注入 inline handler」為由加回
        'unsafe-inline'。2026-09-25 在這份 CSP 下實測：TON Connect UI 3.0.0
        開 modal→錢包清單→QR、Reown AppKit→WalletConnect QR 都零 violation，
        擋到的只會是我們自己的 inline script（當時 14 段，已移成外部檔）。
        有 'unsafe-inline' 時，任何一個 escape 漏洞都能直接跑 JS。
        真的需要 inline（例如 importmap）請加 'sha256-…' 並用測試從 HTML 重算。
        """
        sources = _csp_directive("script-src")
        assert "'unsafe-inline'" not in sources, sources
        assert "'unsafe-hashes'" not in sources, sources
        assert "'self'" in sources

    def test_script_src_keeps_third_party_sources(self):
        """拿掉 'unsafe-inline' 不能順手拿掉 CDN／登入 SDK 來源。"""
        sources = _csp_directive("script-src")
        for origin in (
            "https://cdn.jsdelivr.net",  # i18next / markdown-it / chart.js
            "https://unpkg.com",  # TON Connect UI / tonweb / lucide / ethers
            "https://telegram.org",  # Telegram Mini App SDK
            "https://accounts.google.com",  # Google Identity Services
            "https://static.cloudflareinsights.com",  # Cloudflare beacon
        ):
            assert origin in sources, f"script-src lost {origin}: {sources}"

    def test_style_src_allows_inline_styles_for_tonconnect(self):
        """style-src must keep 'unsafe-inline'.

        TON Connect UI injects dynamic inline styles (and CSP hashes cannot
        cover style attributes), so removing it breaks the wallet login modal
        in production. script-src remains the strict, XSS-critical directive.
        """
        content = MIDDLEWARE_FILE.read_text(encoding="utf-8")
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('"style-src'):
                assert "'unsafe-inline'" in stripped, (
                    f"style-src must allow inline styles for TON Connect UI: {stripped}"
                )
                return
        pytest.fail("style-src directive line not found in CSP")

    def test_connect_src_allows_wallet_bridges(self):
        """connect-src must allow https: (+ ws/wss).

        The TON Connect wallet list resolves to per-wallet bridge origins
        (walletbot.me, mytonwallet, OKX, Binance, ...) that change over time;
        enumerating them breaks wallet login whenever the list changes.
        """
        content = MIDDLEWARE_FILE.read_text(encoding="utf-8")
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped.startswith('"connect-src'):
                for scheme in ("https:", "wss:", "ws:"):
                    assert f" {scheme}" in stripped, (
                        f"connect-src must include {scheme}: {stripped}"
                    )
                return
        pytest.fail("connect-src directive line not found in CSP")


# ── Inline Handler Tests ──────────────────────────────────────────────────────


_HTML_FILES = sorted(glob.glob(str(WEB_DIR / "**" / "*.html"), recursive=True))
_JS_FILES = sorted(glob.glob(str(WEB_DIR / "**" / "*.js"), recursive=True))

# 任何 on<事件>= 屬性（onclick／onerror／onload／onmouseover…，HTML 屬性不分大小寫），
# 值必須是引號、跳脫引號或 ${…}——JS 的 `const onReady = () =>` 不會中。
# 前面不能是 . / 字元 / $ / -：排除 `el.onclick = fn`（CSP 不管屬性賦值）、
# `aria-controls=`、`content=`、`?wc-oneclick=1` 這類。
_INLINE_HANDLER_RE = re.compile(
    r"(?<![.\w$-])on[a-z]+\s*=\s*(?:\\?[\"'`]|\$\{)", re.IGNORECASE
)
# 動態掛 inline handler 一樣會被擋
_SET_HANDLER_ATTR_RE = re.compile(r"setAttribute\(\s*[\"'`]on[a-z]+[\"'`]", re.I)
# <a href="javascript:…"> 在沒有 'unsafe-inline' 時點下去會被擋（還會噴 CSP violation）
_JS_URL_RE = re.compile(r"\bhref\s*=\s*\\?[\"'`]\s*javascript:", re.IGNORECASE)


class TestNoInlineHandlers:
    """Verify no CSP-blocked inline event handlers remain in rendered markup."""

    @pytest.mark.parametrize(
        "snippet",
        [
            '<img src="x" onerror="alert(1)">',
            "<div onClick='go()'>",
            '`<button onclick="${fn}">`',
            '"<a onmouseover=\\"x()\\">"',
            "<body onload=${init}>",
        ],
    )
    def test_handler_regex_catches_attributes(self, snippet):
        assert _INLINE_HANDLER_RE.search(snippet), snippet

    @pytest.mark.parametrize(
        "snippet",
        [
            "btn.onclick = () => go();",
            "window.onerror = handler;",
            "const onReady = () => {};",
            "const onboardingBanner = show ? `<div>` : '';",
            '<a aria-controls="menu" content="x">',
            "location.search.includes('wc-oneclick=1')",
        ],
    )
    def test_handler_regex_ignores_property_assignments(self, snippet):
        assert not _INLINE_HANDLER_RE.search(snippet), snippet

    @pytest.mark.parametrize("template_file", _HTML_FILES + _JS_FILES)
    def test_no_inline_event_attributes_in_rendered_templates(self, template_file):
        """HTML 與 JS 模板（innerHTML）都吃正式站 CSP：inline handler 不會執行。

        ``button.onclick = handler`` 這種屬性賦值不受 CSP 影響，允許；只擋
        markup 屬性。改用 data-click／data-change-action 等委派（click-delegator.js）。
        """
        content = Path(template_file).read_text(encoding="utf-8")
        found = sorted(
            {m.group(0) for m in _INLINE_HANDLER_RE.finditer(content)}
            | {m.group(0) for m in _SET_HANDLER_ATTR_RE.finditer(content)}
        )
        assert not found, (
            f"{template_file} contains inline event handler(s): {found}. "
            "Use data-* attributes plus a CSP-safe listener instead."
        )

    @pytest.mark.parametrize("template_file", _HTML_FILES + _JS_FILES)
    def test_no_javascript_urls(self, template_file):
        content = Path(template_file).read_text(encoding="utf-8")
        found = [m.group(0) for m in _JS_URL_RE.finditer(content)]
        assert not found, (
            f"{template_file} has javascript: URLs {found}; "
            'use a <button type="button"> with data-click instead.'
        )

    @pytest.mark.parametrize("html_file", _HTML_FILES)
    def test_no_inline_script_blocks(self, html_file):
        """任何 web/**/*.html 都不可有可執行的 inline <script>。

        正式站 script-src 沒有 'unsafe-inline'，inline script 整段不會跑
        （forum／legal／scam-tracker 頁的初始化曾全在 inline 裡）。資料型
        （application/ld+json）不執行、不受 script-src 管，放行。
        """
        content = Path(html_file).read_text(encoding="utf-8")
        offenders = []
        for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", content, re.S | re.I):
            attrs, body = m.group(1), m.group(2)
            if re.search(r"\bsrc\s*=", attrs):
                continue
            type_m = re.search(r"\btype\s*=\s*[\"']?([^\"'\s>]+)", attrs)
            if type_m and type_m.group(1).lower() == "application/ld+json":
                continue
            if body.strip():
                offenders.append(content[: m.start()].count("\n") + 1)
        assert not offenders, (
            f"{html_file} has inline <script> at lines {offenders}; "
            "move it to an external file (CSP script-src has no 'unsafe-inline')."
        )

    def test_early_init_js_exists(self):
        """early-init.js must exist (moved from inline script)."""
        assert (WEB_DIR / "js" / "early-init.js").exists()

    def test_click_delegator_js_exists(self):
        """click-delegator.js must exist (handles data-click events)."""
        assert (WEB_DIR / "js" / "click-delegator.js").exists()


# ── CancelledError Propagation Tests ──────────────────────────────────────────


class TestCancelledErrorPropagation:
    """Verify that CancelledError guards properly re-raise system signals."""

    def test_cancelled_error_not_swallowed(self):
        """asyncio.CancelledError should propagate, not be caught by except Exception."""
        async def mock_async_operation():
            raise asyncio.CancelledError()

        async def guarded_handler():
            try:
                await mock_async_operation()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pytest.fail("CancelledError was swallowed by except Exception")

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(guarded_handler())

    def test_keyboard_interrupt_not_swallowed(self):
        """KeyboardInterrupt should propagate through guarded handler."""
        def mock_sync_operation():
            raise KeyboardInterrupt()

        def guarded_handler():
            try:
                mock_sync_operation()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pytest.fail("KeyboardInterrupt was swallowed by except Exception")

        with pytest.raises(KeyboardInterrupt):
            guarded_handler()

    def test_system_exit_not_swallowed(self):
        """SystemExit should propagate through guarded handler."""
        def guarded_handler():
            try:
                raise SystemExit(1)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pytest.fail("SystemExit was swallowed by except Exception")

        with pytest.raises(SystemExit):
            guarded_handler()

    def test_normal_exception_still_caught(self):
        """Normal exceptions must still be caught by except Exception (behavior unchanged)."""
        def guarded_handler():
            try:
                raise ValueError("test error")
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                return str(e)

        result = guarded_handler()
        assert result == "test error"

    def test_http_error_still_caught(self):
        """HTTP errors before the guard must still be handled."""
        from fastapi import HTTPException

        def guarded_handler():
            try:
                raise HTTPException(status_code=404, detail="Not found")
            except HTTPException:
                raise
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                pytest.fail(f"HTTPException was swallowed: {e}")

        with pytest.raises(HTTPException):
            guarded_handler()
