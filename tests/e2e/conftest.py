"""Shared E2E fixtures: static server, context seeding, API stubs."""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.orm.user_api_keys_repo import LLM_PROVIDERS

try:
    from playwright.async_api import BrowserContext, async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    BrowserContext = None
    async_playwright = None

if not HAS_PLAYWRIGHT:
    pytest.skip("playwright not installed", allow_module_level=True)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "web"
PUBLIC_DIR = ROOT / "web" / "public"
HOST = "127.0.0.1"
PORT = 8770
BASE_URL = f"http://{HOST}:{PORT}/static/index.html"

# ---------------------------------------------------------------------------
# Static file server
# ---------------------------------------------------------------------------


class _StaticAppHandler(SimpleHTTPRequestHandler):
    """Serves ``web/`` files under ``/static/`` and at root."""

    def translate_path(self, path: str) -> str:
        clean = urlsplit(path).path
        if clean.startswith("/static/"):
            rel = clean[len("/static/") :]
            target = WEB_DIR / rel
            # Vite build 會把 web/public/* 複製到輸出根（如 sw-register.js），
            # serve web/ 時模擬此映射，避免 /static/sw-register.js 404。
            if not target.exists():
                public_target = PUBLIC_DIR / rel
                if public_target.exists():
                    return str(public_target.resolve())
            return str(target.resolve())
        if clean in ("/", "/index.html"):
            return str((WEB_DIR / "index.html").resolve())
        return str((WEB_DIR / clean.lstrip("/")).resolve())

    def log_message(self, fmt: str, *args) -> None:
        return


@contextlib.contextmanager
def _run_static_server():
    server = ThreadingHTTPServer((HOST, PORT), _StaticAppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Minimal API stub factory
# ---------------------------------------------------------------------------


async def _stub_all_api_routes(route):
    """Intercept every ``/api/`` call and return a safe stub response."""
    parsed = urlsplit(route.request.url)
    path = parsed.path
    method = route.request.method

    stubs: dict[str, object] = {
        "/api/config": {"test_mode": True, "current_settings": {}},
        "/api/model-config": {"model_config": {}},
        "/api/config/limits": {},
        # 形狀必須跟真實回應一致：GET /api/user/api-keys 會替**每一個**支援的
        # provider 補一筆 has_key:false 的空紀錄（user_api_keys_repo 的
        # "Ensure all supported providers have an entry"）。
        #
        # 2026-09-08：這個 stub 原本只給 openai 一筆，於是任何用它的測試都活在
        # 一個「沒綁的 provider 根本不會出現」的假世界裡——正是那個世界讓
        # AI Studio 漏掉 has_key 過濾的 bug（整份型錄都列出來）躲過測試。
        # fixture 比真實世界乾淨，測試就會在保護一個不存在的世界。
        "/api/user/api-keys": {
            "keys": dict(
                {
                    p: {"has_key": False, "masked_key": None, "model": None, "models": []}
                    for p in LLM_PROVIDERS
                },
                openai={
                    "has_key": True,
                    "masked_key": "sk-***e2e",
                    "model": "gpt-4o-mini",
                    "models": ["gpt-4o-mini"],
                },
            )
        },
        "/api/user/tools": {"tools": []},
        "/api/test-mode/current-tier": {"tier": "premium"},
        "/api/messages/limits": {
            "is_premium": True,
            "message_limit": {"used": 0, "limit": 20},
            "max_length": 500,
        },
    }

    if path in stubs:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(stubs[path]),
        )
        return

    if path == "/api/chat/sessions" and method == "GET":
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"sessions": []}),
        )
        return

    if path == "/api/chat/sessions" and method == "POST":
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"session_id": "sess-e2e-001", "title": "E2E"}),
        )
        return

    if path == "/api/analyze" and method == "POST":
        await route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=('data: {"content":"E2E test response."}\n\ndata: {"done":true}\n\n'),
        )
        return

    # Catch-all for /api/* — return empty JSON
    if path.startswith("/api/"):
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({}),
        )
        return

    await route.continue_()


# ---------------------------------------------------------------------------
# Context seeding (localStorage + WebSocket mock)
# ---------------------------------------------------------------------------

MOCK_USER_INIT_SCRIPT = """
localStorage.setItem("ton_user", JSON.stringify({
    uid: "e2e-user-001",
    user_id: "e2e-user-001",
    username: "E2ETester",
    authMethod: "password",
    accessToken: "token-e2e",
    accessTokenExpiry: Date.now() + 3600 * 1000,
    membership_tier: "premium"
}));

class E2EWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor(url) {
        this.url = url;
        this.readyState = E2EWebSocket.OPEN;
        this.onopen = null;
        this.onmessage = null;
        this.onclose = null;
        this.onerror = null;
        setTimeout(() => { if (this.onopen) this.onopen({ type: "open" }); }, 0);
    }

    send() {}

    close() {
        this.readyState = E2EWebSocket.CLOSED;
        if (this.onclose) this.onclose({ type: "close", code: 1000 });
    }

    addEventListener(type, handler) {
        if (type === "open") this.onopen = handler;
        if (type === "message") this.onmessage = handler;
        if (type === "close") this.onclose = handler;
        if (type === "error") this.onerror = handler;
    }
}

window.WebSocket = E2EWebSocket;
"""


async def _seed_context(context: BrowserContext) -> None:
    await context.add_init_script(MOCK_USER_INIT_SCRIPT)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def static_server():
    """Start a static HTTP server once for the entire E2E session."""
    with _run_static_server():
        yield


@pytest.fixture
async def browser(static_server):
    """Launch a headless Chromium browser (closed after each test)."""
    async with async_playwright() as p:
        yield await p.chromium.launch(headless=True)


@pytest.fixture
async def context(browser):
    """Create a fresh browser context with auth seeding and API stubs."""
    ctx = await browser.new_context(viewport={"width": 1440, "height": 960})
    await _seed_context(ctx)
    yield ctx
    await ctx.close()


@pytest.fixture
async def page(context):
    """Create a new page with all API routes stubbed."""
    p = await context.new_page()
    await p.route("**/*", _stub_all_api_routes)
    yield p
    await p.close()
