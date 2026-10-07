"""Google 登入：驗 Google Identity Services 的 ID token（JWKS 驗簽、aud／iss／exp）。

設計：docs/plans/2026-09-13-google-play-twa-design.md §4。只用基本 profile（sub／email／name／
picture／locale），不碰敏感 scope，所以 OAuth 同意畫面不用 Google 審核。
env：``GOOGLE_CLIENT_ID``（Web 用戶端 ID；TWA 內的 Chrome 也用同一個）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

import jwt
from jwt import PyJWKClient

logger = logging.getLogger(__name__)

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
_jwk_client: Optional[PyJWKClient] = None


class GoogleAuthError(ValueError):
    """token 壞掉／不是給我們的／過期（訊息可給使用者看，不含內部細節）。"""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: Optional[str]
    email_verified: bool
    name: Optional[str]
    picture: Optional[str]
    locale: Optional[str]


def client_id() -> str:
    return os.getenv("GOOGLE_CLIENT_ID", "").strip()


def enabled() -> bool:
    return bool(client_id())


def _jwks() -> PyJWKClient:
    global _jwk_client
    if _jwk_client is None:
        # PyJWKClient 內建快取（預設 300s、最多 16 把），Google 的 key 每天輪一次
        _jwk_client = PyJWKClient(GOOGLE_JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwk_client


def verify_id_token(token: str, *, audience: Optional[str] = None) -> GoogleIdentity:
    """驗簽＋標準檢查。呼叫端在 run_sync 裡跑（會打一次 JWKS，之後走快取）。"""
    aud = audience or client_id()
    if not aud:
        raise GoogleAuthError("Google sign-in is not configured")
    if not isinstance(token, str) or token.count(".") != 2 or len(token) > 4096:
        raise GoogleAuthError("Invalid Google token")
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=aud,
            options={"require": ["exp", "iat", "sub", "iss"]},
            leeway=60,
        )
    except jwt.ExpiredSignatureError as exc:
        raise GoogleAuthError("Google token expired, please sign in again") from exc
    except jwt.InvalidAudienceError as exc:
        raise GoogleAuthError("Google token is not for this app") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        logger.info("[google] id token rejected: %s", type(exc).__name__)
        raise GoogleAuthError("Invalid Google token") from exc
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleAuthError("Invalid Google token issuer")
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise GoogleAuthError("Invalid Google token")
    return GoogleIdentity(
        sub=sub,
        email=(claims.get("email") or None),
        email_verified=bool(claims.get("email_verified")),
        name=(claims.get("name") or None),
        picture=(claims.get("picture") or None),
        locale=(claims.get("locale") or None),
    )


def reset_jwks_cache() -> None:
    global _jwk_client
    _jwk_client = None
