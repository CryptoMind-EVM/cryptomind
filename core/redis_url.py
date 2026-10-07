"""
Redis connection URL resolver.

Supports both:
- REDIS_URL (preferred)
- REDIS_HOST (+ optional REDIS_PORT/REDIS_DB/REDIS_PASSWORD/REDIS_USERNAME)
"""

from __future__ import annotations

import os
from typing import Tuple
from urllib.parse import quote, urlparse


def redact_redis_url(url: str) -> str:
    """將 Redis URL 內的密碼遮蔽，供 log 使用。

    redis://:secret@host:6379/0  →  redis://***@host:6379/0
    redis://user:secret@host     →  redis://user:***@host
    解析失敗時退回固定佔位字串，絕不丟出原 URL。
    """
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            if parsed.username:
                netloc = f"{parsed.username}:***@{netloc}"
            else:
                netloc = f"***@{netloc}"
            return f"{parsed.scheme}://{netloc}/{parsed.path.lstrip('/')}"
        return url  # 無密碼，原樣安全
    except Exception:
        return "redis://***"


def resolve_redis_url() -> Tuple[str, str]:
    """
    Resolve redis URL from environment variables.

    Returns:
        (redis_url, source_env_name)
        source_env_name is one of: "REDIS_URL", "REDIS_HOST", or "".
    """
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if redis_url:
        return redis_url, "REDIS_URL"

    redis_host = (os.getenv("REDIS_HOST") or "").strip()
    if not redis_host:
        return "", ""

    redis_port = (os.getenv("REDIS_PORT") or "6379").strip() or "6379"
    redis_db = (os.getenv("REDIS_DB") or "0").strip() or "0"
    redis_username = (os.getenv("REDIS_USERNAME") or "").strip()
    redis_password = (os.getenv("REDIS_PASSWORD") or "").strip()

    auth = ""
    if redis_username and redis_password:
        auth = f"{quote(redis_username)}:{quote(redis_password)}@"
    elif redis_password:
        auth = f":{quote(redis_password)}@"
    elif redis_username:
        auth = f"{quote(redis_username)}@"

    return f"redis://{auth}{redis_host}:{redis_port}/{redis_db}", "REDIS_HOST"
