"""對外站台 base URL（不含尾斜線）——TON manifest、Farcaster manifest、sitemap 共用。

從 api_server 搬出來（2026-09-13），讓 router 也能用而不繞回主程式。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException, Request

from core.config import SITE_URL

logger = logging.getLogger("API")

_IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() in {
    "production",
    "prod",
}


def resolve_public_base(request: Request) -> str:
    """
    回傳對外的站台 base URL（不含尾斜線），給站點層級 manifest 用。

    Zeabur（與任何 TLS-terminating proxy）用純 HTTP 連進容器，而 gunicorn 的
    forwarded_allow_ips 預設只信 127.0.0.1 → request.base_url 拿到的是
    "http://..."。manifest 發出 http 但站台是 https 時，錢包掃完 QR / PWA
    安裝都會壞掉（9377ffd 踩過的線上登入斷線）。所以 scheme 一律以
    X-Forwarded-Proto 第一跳為準；production 缺標頭時保底 https。

    SITE_URL 只當對照（不符時 log warning），實際採用請求來源
    的 host（更可靠：Zeabur 改網域時不靠手動同步 env）。
    """
    request_base = str(request.base_url).rstrip("/")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    forwarded_proto = forwarded_proto.split(",")[0].strip().lower()
    if forwarded_proto not in ("http", "https") and _IS_PRODUCTION:
        # 保底：production 一律走 HTTPS。就算某層 proxy 沒帶 X-Forwarded-Proto，
        # 也絕不能發出 http 的 manifest 把登入/安裝弄掛。
        forwarded_proto = "https"
    if forwarded_proto in ("http", "https"):
        request_base = urlunparse(
            urlparse(request_base)._replace(scheme=forwarded_proto)
        ).rstrip("/")
    env_base = SITE_URL.rstrip("/")
    if env_base and env_base != request_base:
        logger.warning(
            "[manifest] SITE_URL (%s) != request host (%s); using request host",
            env_base,
            request_base,
        )
    return request_base or env_base


def public_base_url() -> str:
    """沒有 request 可看時（cron、背景工作）的對外 base：``PUBLIC_BASE_URL`` env 優先，
    否則 ``SITE_URL``（沒設時沿用 TON_MANIFEST_URL）。不含尾斜線。"""
    import os

    return (os.getenv("PUBLIC_BASE_URL", "").strip() or SITE_URL).rstrip("/")


def expected_site_domain() -> str:
    """Return the only origin (``host[:port]``) that signed logins may claim.

    Moved from the TON-era ``_expected_ton_proof_domain`` (that module was
    removed 2026-09-26). Used as the EVM (SIWE) login fallback domain.

    Wallets sign with ``window.location.host`` — the host *with* its port
    whenever the port is non-default (e.g. ``localhost:8080``). We must
    therefore compare against the site URL's ``netloc`` (host[:port]), not
    ``hostname``: ``urlparse().hostname`` drops the port, so a ported
    dev/staging origin would mismatch and 401 every login (regression from
    commit 25617cd). On default ports (443/80) browsers omit the port, so
    ``netloc`` for a plain ``https://getcryptomind.com`` is just the bare
    host — prod is unaffected.
    """
    parsed = urlparse(SITE_URL)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    is_production = os.getenv("ENVIRONMENT", "development").lower() in {
        "production",
        "prod",
    }
    if not hostname or (is_production and parsed.scheme != "https"):
        raise HTTPException(status_code=500, detail="Site URL is misconfigured")
    # host[:port] exactly as the browser reports it (strip any userinfo).
    return parsed.netloc.rsplit("@", 1)[-1].rstrip(".").lower()
