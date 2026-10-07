"""
API Rate Limiting Middleware

Implements per-IP and per-user rate limiting to prevent API abuse and DDoS attacks.

Stage 2 Security: Added persistent rate limiting that survives server restarts.
"""

import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from core.redis_url import resolve_redis_url

logger = logging.getLogger(__name__)


def get_user_identifier(request: Request) -> str:
    """
    Get identifier for rate limiting
    - Use user_id from JWT if authenticated
    - Fallback to IP address for unauthenticated requests

    注意：slowapi 的 key_func 在 DI 之前跑，request.state.user 尚未設定，
    故用 best_effort_request_user 從 cookie/Bearer 解 JWT 拿 user_id。
    這讓限流維度是 per-user 而非 per-IP（避免 NAT/共用 IP 用戶共享配額）。
    """
    # Try request.state.user first（若被其他 middleware 設過）
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict):
        user_id = user.get("user_id", "unknown")
        return f"user:{user_id}"

    # DI 之前 best-effort 解 JWT
    try:
        from api.deps import best_effort_request_user

        user = best_effort_request_user(request)
        if user and isinstance(user, dict):
            return f"user:{user.get('user_id', 'unknown')}"
    except Exception:
        pass

    # Telegram bot 代使用者呼叫：不帶 JWT，退回 IP 的話就是 bot 容器的一個 IP——
    # 全體 Telegram 使用者共用一份配額（2026-09-24：bot_chat 10/min 是大家合計）。
    bot_key = _bot_telegram_user_key(request)
    if bot_key:
        return bot_key

    # Fallback to IP address for unauthenticated requests
    ip = get_remote_address(request)
    return f"ip:{ip}"


def _bot_telegram_user_key(request: Request) -> Optional[str]:
    """X-Bot-Secret 驗過才採信 bot 附上的 X-Telegram-User-Id；否則回 None（照舊用 IP）。"""
    expected = os.getenv("BOT_INTERNAL_SECRET", "")
    given = request.headers.get("x-bot-secret", "")
    if not expected or not given or not hmac.compare_digest(given, expected):
        return None
    tg_id = request.headers.get("x-telegram-user-id", "")
    if not tg_id.isdigit() or len(tg_id) > 20:
        return None
    return f"tg:{tg_id}"


def get_rate_limit_storage() -> tuple[str, str]:
    """Resolve shared rate-limit storage and fail closed in production."""
    redis_url, source = resolve_redis_url()
    is_production = os.getenv("ENVIRONMENT", "development").lower() in {
        "production",
        "prod",
    }
    if redis_url and (not is_production or redis_url != "memory://"):
        return redis_url, source
    if is_production:
        raise RuntimeError(
            "REDIS_URL or REDIS_HOST is required in production for shared rate limiting."
        )
    return "memory://", "memory"


# Production must use Redis so rate limits survive restarts and span workers.
REDIS_URL, _redis_source = get_rate_limit_storage()
if _redis_source == "memory":
    logger.warning(
        "REDIS_URL/REDIS_HOST not set; development rate limiting uses memory://"
    )
else:
    logger.info("Rate limiter storage configured via %s", _redis_source)

limiter = Limiter(
    key_func=get_user_identifier,
    default_limits=["1000/hour", "100/minute"],  # Global default limits
    storage_uri=REDIS_URL,
    strategy="fixed-window",  # Fixed window strategy
)


# Custom rate limits for different endpoint types
RATE_LIMITS = {
    # Authentication endpoints (very strict to prevent brute force)
    "auth": "5/minute",
    # Write operations (stricter)
    "write": "30/minute",
    # Payment operations (strict to prevent abuse)
    "payment": "10/minute",
    # Read operations (more lenient)
    "read": "100/minute",
    # Admin operations (moderate)
    "admin": "50/hour",
    # Public endpoints (lenient)
    "public": "200/minute",
    # AI Analysis endpoint (moderate - LLM calls are expensive)
    "analysis": "10/minute",
    # Community Governance endpoints
    "governance_report": "10/hour",  # Report submission (strict)
    "governance_vote": "30/hour",  # Voting (Premium members only)
    "governance_read": "100/hour",  # Reading governance data
    # Sensitive API Key endpoints
    "api_key_write": "20/minute",  # Saving/deleting API keys
    # Token refresh (moderate - prevent token abuse)
    "token_refresh": "5/minute",  # Refresh token endpoint
}


def get_rate_limit_for_route(request: Request) -> str:
    """
    [DEPRECATED] Determine appropriate rate limit based on route and method.

    This function is no longer used in production. All routes now use
    @limiter.limit() decorators directly. Kept for backward compatibility
    with existing tests only.
    """
    method = request.method.lower()
    path = request.url.path.lower()

    # Authentication endpoints
    if any(x in path for x in ["/login", "/pi-sync", "/dev-login"]):
        return RATE_LIMITS["auth"]

    # Sensitive API Key endpoints (very strict)
    if "/api-keys" in path:
        # Write operations (save/delete)
        if method in ["post", "put", "delete"]:
            return RATE_LIMITS["api_key_write"]
        # Read operations (masked keys)
        return RATE_LIMITS["read"]

    # Tip endpoints
    if "/tip" in path:
        return RATE_LIMITS["payment"]

    # Admin endpoints
    if "/admin" in path:
        return RATE_LIMITS["admin"]

    # Write operations
    if method in ["post", "put", "patch", "delete"]:
        return RATE_LIMITS["write"]

    # AI Analysis endpoint (expensive LLM calls)
    if "/analyze" in path or "/backtest" in path:
        return RATE_LIMITS["analysis"]

    # Public read endpoints (forum, market data)
    if any(x in path for x in ["/forum/posts", "/forum/boards", "/market", "/agents/"]):
        return RATE_LIMITS["public"]

    # Community Governance endpoints
    if "/governance" in path:
        if "/reports" in path and method == "post":
            return RATE_LIMITS["governance_report"]
        elif "/vote" in path and method == "post":
            return RATE_LIMITS["governance_vote"]
        else:
            return RATE_LIMITS["governance_read"]

    # Default read operations
    if method == "get":
        return RATE_LIMITS["read"]

    # Default fallback
    return "100/minute"


# Exception handler for rate limit exceeded
async def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """
    Custom handler for rate limit exceeded errors

    Returns a JSON response with retry information.

    Note: ``exc.retry_after`` 在某些 slowapi 版本不存在（RateLimitExceeded 只保證
    有 ``limit`` 屬性）。用 ``getattr`` 安全存取；缺時 fallback 到保守估值 60 秒，
    避免 handler 本身在限流觸發時丟 AttributeError 把 429 變成 500。
    """
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        retry_after = 60
    return JSONResponse(
        status_code=429,
        content={
            "error": "Rate limit exceeded",
            "message": "Too many requests. Please slow down and try again later.",
            "retry_after": retry_after,
            "limit": str(exc.limit),
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(exc.limit),
            "X-RateLimit-Remaining": "0",
        },
    )


# ============================================================================
# Stage 2 Security: Persistent Rate Limiting
# ============================================================================


class PersistentRateLimiter:
    """
    File-based persistent rate limiter that survives server restarts.

    This provides a fallback for environments without Redis.
    Rate limits are stored in a JSON file and loaded on startup.

    Usage:
        # Initialize
        limiter = PersistentRateLimiter("data/rate_limits.json")

        # Check limit
        if limiter.check_limit("user:123", limit=100, window=3600):
            # Allow request
        else:
            # Rate limit exceeded
    """

    def __init__(self, storage_path: str = "data/rate_limits.json"):
        """
        Initialize the persistent rate limiter.

        Args:
            storage_path: Path to the JSON file for storing rate limit state
        """
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict = {}
        self._load_state()

    def _load_state(self):
        """Load rate limit state from file."""
        try:
            if self.storage_path.exists():
                with open(self.storage_path, "r") as f:
                    self.state = json.load(f)
        except (json.JSONDecodeError, IOError):
            # If file is corrupted, start fresh
            self.state = {}

    def _save_state(self):
        """Save rate limit state to file."""
        try:
            with open(self.storage_path, "w") as f:
                json.dump(self.state, f)
        except IOError:
            # Rate limiting is a protection, not a requirement; log for debugging
            logger.debug("Failed to persist rate limit state to %s", self.storage_path)

    def check_limit(self, key: str, limit: int, window: int) -> bool:
        """
        Check if a request should be rate limited.

        Args:
            key: Unique identifier (e.g., "user:123" or "ip:192.168.1.1")
            limit: Maximum number of requests allowed
            window: Time window in seconds

        Returns:
            True if request is allowed, False if rate limit exceeded
        """
        now = int(time.time())
        window_start = now - window

        # Initialize key if not exists
        if key not in self.state:
            self.state[key] = []

        # Remove timestamps outside the current window
        self.state[key] = [t for t in self.state[key] if t > window_start]

        # Check if limit exceeded
        if len(self.state[key]) >= limit:
            return False

        # Add current request timestamp
        self.state[key].append(now)
        self._save_state()
        return True

    def get_remaining(self, key: str, limit: int, window: int) -> int:
        """
        Get remaining requests for a key.

        Args:
            key: Unique identifier
            limit: Maximum number of requests
            window: Time window in seconds

        Returns:
            Number of remaining requests
        """
        now = int(time.time())
        window_start = now - window

        if key not in self.state:
            return limit

        # Count requests within window
        recent_count = sum(1 for t in self.state[key] if t > window_start)
        return max(0, limit - recent_count)

    def reset_key(self, key: str):
        """
        Reset rate limit for a specific key.

        Args:
            key: Unique identifier to reset
        """
        if key in self.state:
            del self.state[key]
            self._save_state()
