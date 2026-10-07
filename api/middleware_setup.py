"""Middleware registration — CORS, GZip, rate limiting, security headers, etc."""

import os
import re
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from api.deps import ACCESS_TOKEN_COOKIE
from api.utils import logger
from core import miniapp


def get_security_config() -> dict:
    """Read security middleware settings and reject unsafe production defaults."""
    is_production = os.getenv("ENVIRONMENT", "development").lower() in {
        "production",
        "prod",
    }
    origins = [
        # Strip trailing slash: browsers send Origin without one
        # (https://host), so an allowlist entry like https://host/ would never
        # match and silently block legit cross-origin requests.
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")
        if origin.strip()
    ]
    allowed_hosts = [
        host.strip()
        for host in os.getenv("ALLOWED_HOSTS", "").split(",")
        if host.strip()
    ]

    if is_production:
        if not origins or "*" in origins:
            raise RuntimeError(
                "CORS_ORIGINS must list explicit HTTPS origins in production."
            )
        if not allowed_hosts:
            raise RuntimeError(
                "ALLOWED_HOSTS is required in production to enable host-header protection."
            )

    return {
        "is_production": is_production,
        "origins": origins,
        "allowed_hosts": allowed_hosts,
    }


_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# vite 預設輸出檔名 <name>-<8 碼內容 hash>.js/.css（含 sourcemap）。正式 image 把
# dist/static/assets 複製到 web/assets，同時由 /static/assets/ 與 /assets/ 兩個掛載點服務。
# 路徑段不可以 . 開頭：擋掉 /static/assets/../js/x-12345678.js 這類跳出 assets 的路徑。
_HASHED_ASSET_RE = re.compile(
    r"^/(?:static/)?assets/(?:[\w-][\w.-]*/)*[\w-][\w.-]*-[A-Za-z0-9_-]{8}\.(?:js|css)(?:\.map)?$"
)


def _is_hashed_build_asset(path: str) -> bool:
    """帶內容 hash 的打包檔（檔名一變內容就不同，可以長期快取）。"""
    return bool(_HASHED_ASSET_RE.match(path))


def _origin_allowed(origin: str, request: Request, allowed: set[str]) -> bool:
    """Origin 的 host 與請求 Host 相同（本站）、或在 CORS 允許清單裡。"""
    if origin.rstrip("/") in allowed:
        return True
    origin_host = urlsplit(origin).netloc.lower()
    request_host = (request.headers.get("host") or "").lower()
    return bool(origin_host) and origin_host == request_host


def content_security_policy() -> str:
    """Production CSP. frame-ancestors 放行 mini app 宿主（Base App／Farcaster／Telegram
    網頁版都用 iframe 載我們；只給 'self' 會整個載不出來），其餘網域仍不能框。"""
    frame_ancestors = " ".join(["'self'", *miniapp.FRAME_ANCESTORS])
    return (
        "default-src 'self'; "
        # 沒有 'unsafe-inline'（2026-09-25）：inline <script>、on*= 屬性、javascript:
        # 網址一律不執行——按鈕走 click-delegator.js 的 data-click 委派，頁面腳本
        # 放外部檔。tests/security/test_csp_and_exception_regression.py 看守。
        "script-src 'self' "
        "https://cdn.jsdelivr.net https://unpkg.com "
        "https://cdnjs.cloudflare.com https://telegram.org https://accounts.google.com "
        # Cloudflare Web Analytics 的自動注入 beacon（2026-09-21 DANNY 決定保留）；
        # beacon 回報走 cloudflareinsights.com，connect-src 已放行 https:
        "https://static.cloudflareinsights.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://unpkg.com https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com https://fonts.reown.com; "
        "img-src 'self' data: blob: https:; "
        "connect-src 'self' https: wss: ws:; "
        "worker-src 'self'; "
        "frame-src 'self' https://verify.walletconnect.org https://walletconnect.com https://*.walletconnect.org https://accounts.google.com; "
        f"frame-ancestors {frame_ancestors}"
    )


def setup_middleware(app: FastAPI) -> None:
    """Register all middleware and exception handlers on the FastAPI app."""

    security_config = get_security_config()
    is_production = security_config["is_production"]

    # --- Global Exception Handler (Fix 500 Internal Server Error) ---
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """
        Catch-all exception handler to ensure all 500 errors return JSON
        and are properly logged with traceback.

        Stage 2 Security: Hide error details in production to prevent
        information leakage.
        """
        import traceback

        error_msg = f"{type(exc).__name__}: {str(exc)}"

        # Log full details for debugging
        logger.error(
            f"🔥 Unhandled 500 Error at {request.method} {request.url.path}: {error_msg}"
        )
        if not is_production:
            logger.error(traceback.format_exc())

        # Response varies by environment - hide details in production
        # Uses structured APIErrorResponse format for consistency
        response_content = {
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An error occurred" if is_production else error_msg,
                "path": request.url.path,
            },
        }

        return JSONResponse(status_code=500, content=response_content)

    # ================================================================
    # Security Enhancements (Phase 7)
    # ================================================================

    # --- 1. Rate Limiting ---
    try:
        from slowapi.errors import RateLimitExceeded

        from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler

        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
        logger.info("✅ Rate limiting enabled")
    except ImportError as e:
        logger.warning(f"⚠️ Rate limiting not available: {e}")
        logger.warning("Install slowapi: pip install slowapi")

    # --- 2. Audit Logging Middleware ---
    try:
        from api.middleware.audit import audit_middleware

        @app.middleware("http")
        async def audit_logging(request, call_next):
            """Audit all API requests"""
            return await audit_middleware(request, call_next)

        logger.info("✅ Audit logging enabled")
    except ImportError as e:
        logger.warning(f"⚠️ Audit logging not available: {e}")

    # --- 3. CORS ---
    # 🔒 Security: Read allowed origins from environment variable
    # Default to localhost for development, production MUST override this
    origins = security_config["origins"]

    # Security check: warn if wildcard is accidentally configured
    if "*" in origins or "" in origins:
        logger.warning(
            "⚠️ SECURITY: Wildcard CORS origin detected!"
            " This should NOT be used in production."
        )

    logger.info(f"🔒 CORS allowed origins: {origins}")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-OKX-API-KEY",
        ],
    )

    # --- 4. GZip Compression (Performance Optimization) ---
    # 自動壓縮大於1KB的響應，減少帶寬消耗，提升加載速度
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    logger.info("✅ GZip compression enabled")

    # --- 5. Security Headers Middleware (Stage 2 Security) ---
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """
        Stage 2 Security: Add security headers to all responses.

        Headers added:
        - X-Content-Type-Options: Prevent MIME type sniffing
        - X-Frame-Options: Prevent clickjacking
        - X-XSS-Protection: Enable XSS filter
        - Referrer-Policy: Control referrer information
        - Strict-Transport-Security (production): Force HTTPS
        - Content-Security-Policy (production): Control resource loading
        """
        response = await call_next(request)

        # Cache-Control for static assets
        # 全部 no-store（含 HTML）：2026-09-11 DANNY 事件——HTML 原本刻意
        # 只用 no-cache 以允許錢包 WebView 的 BFCache 返回還原（Tonkeeper
        # 等 TON 錢包切回來能還原完整 JS 狀態），但 BFCache 會還原「部署
        # 前的整頁舊快照」：使用者在新舊頁面間擺盪（同一 session 一張截圖
        # 有新功能、一張沒有），舊版付款 UI 直接造成重複訂單與誤導。付款
        # 頁正確性 > 返回還原體驗；登入採 HttpOnly cookie，重載不影響。
        # 非 HTML 的靜態檔（未打包的 JS/CSS/JSON/圖片）用 no-cache：每次使用前都向伺服器
        # 重驗（ETag），內容沒變只回 304、不重傳，內容變了就拿新的——跟 no-store 一樣不會拿到
        # 舊版，但不必每頁重下載（2026-09-26 量測：每頁約 935 KB，同一張圖一頁載 3 次）。
        # BFCache 只看主文件（HTML）的 no-store，所以 9/11 的決定只需要套在 HTML 上。
        # 加 private：Cloudflare 的 Browser Cache TTL（最少 4 小時）會把單純的 no-cache 改寫成
        # max-age=14400 並在邊緣快取（#893 上線實測，已由 #894 revert）；private 表示只准使用者
        # 自己的瀏覽器存，CDN 不能存。
        _static_prefixes = ("/static/", "/js/", "/css/", "/scam-tracker/")
        path = request.url.path
        if response.status_code == 404 and path.endswith((".js", ".css", ".map")):
            # Cloudflare 連 .js 的 404 也會快取（2026-10-06 實測），還會叫瀏覽器記 4 小時：
            # 部署換容器的空窗，/sw.js 或舊 chunk 只要被存下一次 404，使用者就黏住到過期。
            # 任何 .js／.css／.map 的 404 一律不准存（優先於下面各分支）。
            response.headers["Cache-Control"] = "no-store"
        elif _is_hashed_build_asset(path) and response.status_code == 200:
            # vite 打包輸出（dist/static/assets → web/assets）的檔名帶內容 hash：
            # 內容一變檔名就換，舊 HTML 不會拿到新檔、新 HTML 也不會拿到舊檔，
            # 所以可以讓瀏覽器與 Cloudflare 快取一年，不必每次都回到 VM。
            # 上面那條 9/11 的 no-store 是為了 HTML／未帶 hash 的檔案，不受影響。
            # 只放行 200：部署後舊頁面 lazy-load 已不存在的 chunk 會 404，不能被快取一年。
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif any(path.startswith(p) for p in _static_prefixes):
            if "text/html" in response.headers.get("content-type", ""):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            else:
                response.headers["Cache-Control"] = "private, no-cache"

        # 上架素材（/img/miniapp/ 給 Base App、Farcaster 客戶端與 Base Dashboard；
        # /img/store/ 給 tApps 等商店送審時從第三方頁面跨站抓）；純圖片，開 CORS
        # 沒有風險（2026-09-13）。其他路徑不動。
        if request.url.path.startswith(("/img/miniapp/", "/img/store/")):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Cache-Control"] = "public, max-age=3600"

        # Basic security headers (always on)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options intentionally omitted — TON wallet apps load DApps in
        # WebView; frame-ancestors is controlled via CSP below.
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Production-only headers (require HTTPS)
        if is_production:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
            # script-src stays strict (no inline scripts) — that is the
            # XSS-critical directive. style-src needs 'unsafe-inline' because
            # TON Connect UI injects dynamic inline styles (CSP hashes cannot
            # cover style attributes), and connect-src needs https: because the
            # TON Connect wallet list resolves to per-wallet bridge origins
            # that change as wallets are added.
            response.headers["Content-Security-Policy"] = content_security_policy()

        return response

    logger.info("✅ Security headers enabled")

    # --- 5b. CSRF：Origin 檢查（cookie 改 SameSite=None 後的補償） ---
    # 帶著我們 auth cookie 的寫入請求（POST/PUT/PATCH/DELETE），Origin 必須是
    # 本站或 CORS_ORIGINS 允許的來源；沒有 Origin（curl、bot 服務端呼叫）或沒帶 cookie
    # （Bearer）不管。mini app 在 iframe 裡打 API，Origin 仍是我們自己的網域，會過。
    allowed_origins = set(security_config["origins"])

    @app.middleware("http")
    async def csrf_origin_middleware(request: Request, call_next):
        if request.method in _UNSAFE_METHODS and request.cookies.get(
            ACCESS_TOKEN_COOKIE
        ):
            origin = request.headers.get("origin", "")
            if origin and not _origin_allowed(origin, request, allowed_origins):
                logger.warning(
                    "CSRF blocked: origin=%s host=%s path=%s",
                    origin,
                    request.headers.get("host", ""),
                    request.url.path,
                )
                return JSONResponse(
                    status_code=403, content={"detail": "Cross-site request blocked"}
                )
        return await call_next(request)

    logger.info("✅ CSRF origin check enabled")

    # --- 6. TrustedHostMiddleware (production) ---
    if is_production:
        allowed_hosts = security_config["allowed_hosts"]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
        logger.info("✅ TrustedHostMiddleware enabled: %s", allowed_hosts)

    # --- 7. Request ID Middleware ---
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    logger.info("✅ Request ID middleware enabled")
