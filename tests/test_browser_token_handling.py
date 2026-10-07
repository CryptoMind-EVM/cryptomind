"""Regression checks for browser session-token exposure."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_browser_auth_session_does_not_store_jwts():
    source = (PROJECT_ROOT / "web/js/auth.js").read_text(encoding="utf-8")

    assert "accessToken: result.access_token" not in source
    assert "localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(safe))" in source
    assert "delete safe.accessToken" in source
    assert "delete parsedUser.accessToken" in source


def test_static_pages_do_not_read_browser_jwts():
    # post.html 的頁面腳本 2026-09-25 移到 forum/js/post-page.js（CSP 不放行 inline）
    source = (PROJECT_ROOT / "web/forum/post.html").read_text(encoding="utf-8") + (
        PROJECT_ROOT / "web/forum/js/post-page.js"
    ).read_text(encoding="utf-8")

    assert "localStorage.getItem('access_token')" not in source
    assert "credentials: 'include'" in source


def test_login_responses_do_not_return_jwts():
    source = (PROJECT_ROOT / "api/routers/user.py").read_text(encoding="utf-8")

    assert '"access_token": access_token' not in source
    assert '"refresh_token": refresh_token' not in source


def test_authenticated_debug_endpoint_does_not_expose_api_key_material():
    source = (PROJECT_ROOT / "api/routers/user.py").read_text(encoding="utf-8")

    assert '"key_preview"' not in source


def test_websocket_endpoints_accept_cookie_auth():
    """Frontend no longer sends JWTs; both WS endpoints must fall back to the cookie."""
    for router in ("api/routers/notifications.py", "api/routers/messages.py"):
        source = (PROJECT_ROOT / router).read_text(encoding="utf-8")

        assert "websocket.cookies.get(ACCESS_TOKEN_COOKIE)" in source, router


def test_notification_service_does_not_send_browser_jwts():
    source = (PROJECT_ROOT / "web/js/notification-service.js").read_text(
        encoding="utf-8"
    )

    assert "token }" not in source
    assert "AuthManager.currentUser.accessToken" not in source
