"""Base App／Farcaster mini app：manifest 與嵌入 meta（純函式，路由與首頁注入共用）。

DANNY 2026-09-13：「Base App 做。」Base App 是 Farcaster 客戶端，上架＝在自己網域放簽過名的
``/.well-known/farcaster.json`` ＋ 頁面帶 ``fc:miniapp`` meta，再用 Farcaster manifest 工具簽一次網域擁有權
（``accountAssociation`` 見 ``SIGNED_ASSOCIATIONS``，env 可覆蓋）。規格：miniapps.farcaster.xyz/docs/specification。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

APP_NAME = "CryptoMind"  # ≤32
SUBTITLE = "AI analyst, stocks and crypto"  # ≤30，不能有 emoji
DESCRIPTION = (
    "AI agent that tracks your Taiwan, US and crypto holdings in one ledger, "
    "verifies wallets on-chain, and sends a daily brief with earnings and unlock dates."
)  # ≤170
TAGLINE = "Your holdings, one AI analyst"  # ≤30
OG_TITLE = "CryptoMind on Base"  # ≤30
OG_DESCRIPTION = "One ledger for stocks and crypto, verified on-chain, with a daily AI brief."  # ≤100
PRIMARY_CATEGORY = "finance"
TAGS: List[str] = ["ai", "portfolio", "crypto", "stocks", "usdc"]  # ≤5，每個 ≤20
SPLASH_BACKGROUND = "#0b0f19"
REQUIRED_CHAINS = ["eip155:8453"]  # Base
BUTTON_TITLE = "Open CryptoMind"  # ≤32
# 網頁版宿主把 mini app 放 iframe（手機版是 WebView，不受 frame-ancestors 影響）；
# CSP frame-ancestors 只放行這些網域，其餘一律不能框我們（clickjacking）。
# Telegram Web（web.telegram.org/k、/a）載 TMA 也是 iframe，一併列入。
FRAME_ANCESTORS: List[str] = [
    "https://farcaster.xyz",
    "https://*.farcaster.xyz",
    "https://warpcast.com",
    "https://*.warpcast.com",
    "https://base.app",
    "https://*.base.app",
    "https://base.org",
    "https://*.base.org",
    "https://base.dev",
    "https://*.base.dev",
    "https://wallet.coinbase.com",
    "https://*.coinbase.com",
    "https://web.telegram.org",
    "https://*.telegram.org",
]
VALID_CATEGORIES = {
    "games",
    "social",
    "finance",
    "utility",
    "productivity",
    "health-fitness",
    "news-media",
    "music",
    "shopping",
    "education",
    "developer-tools",
    "entertainment",
    "art-creativity",
}


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def enabled() -> bool:
    return _env("MINIAPP_ENABLED").lower() not in ("0", "false", "no", "off")


# 網域 → 已簽好的 accountAssociation。本來就公開在 manifest 裡，不是 secret；寫在程式裡
# 是為了不用動正式站 env（改 .env.production 出過兩次黏行停機）。簽名只對 payload 的網域有效，
# 所以只在該網域出 manifest 時帶。2026-09-24 DANNY（FID 3352447）在
# farcaster.xyz/~/developers/mini-apps/manifest 簽的。
SIGNED_ASSOCIATIONS: Dict[str, Dict[str, str]] = {
    "getcryptomind.com": {
        "header": "eyJmaWQiOjMzNTI0NDcsInR5cGUiOiJjdXN0b2R5Iiwia2V5IjoiMHhhNmUzYTEyODFjRjk4YzYwZmJCMTc5NmZhNTVBMDJjRGQ0MjU0OWExIn0",
        "payload": "eyJkb21haW4iOiJnZXRjcnlwdG9taW5kLmNvbSJ9",
        "signature": "PhETs33oqAv4OFVO59zbGLXdDH/z6DcYpLSz0qdyyOUZ/9qqYZWRIj7vZSnef/O3zCg7cs+0hGnneizmV7Nstxw=",
    },
}


def account_association(domain: Optional[str] = None) -> Optional[Dict[str, str]]:
    """Farcaster manifest 工具簽出的三段：env 三段齊全優先，否則用 ``SIGNED_ASSOCIATIONS[domain]``；都沒有就不放。

    只影響 Farcaster 客戶端的搜尋收錄；Base App 2026-04-09 起不讀 manifest，改看 Base Dashboard。
    """
    header, payload, signature = (
        _env("FARCASTER_ACCOUNT_ASSOCIATION_HEADER"),
        _env("FARCASTER_ACCOUNT_ASSOCIATION_PAYLOAD"),
        _env("FARCASTER_ACCOUNT_ASSOCIATION_SIGNATURE"),
    )
    if header and payload and signature:
        return {"header": header, "payload": payload, "signature": signature}
    return SIGNED_ASSOCIATIONS.get(domain or "")


def builder_addresses() -> List[str]:
    raw = _env("BASE_BUILDER_ALLOWED_ADDRESSES")
    return [a.strip() for a in raw.split(",") if a.strip()]


def notifications_enabled() -> bool:
    """MINIAPP_NOTIFICATIONS_ENABLED=false 就不掛 webhookUrl（宿主不會發 token）。預設開。"""
    return _env("MINIAPP_NOTIFICATIONS_ENABLED").lower() not in ("0", "false", "no", "off")


def is_production() -> bool:
    return os.getenv("ENVIRONMENT", "development").lower() in {"production", "prod"}


def build_manifest(base: str) -> Dict[str, Any]:
    """``/.well-known/farcaster.json``。``base`` 不含尾斜線。"""
    base = base.rstrip("/")
    img = f"{base}/img/miniapp"
    app: Dict[str, Any] = {
        "version": "1",
        "name": APP_NAME,
        "homeUrl": f"{base}/",
        "iconUrl": f"{img}/icon-1024.png",
        "splashImageUrl": f"{img}/splash-200.png",
        "splashBackgroundColor": SPLASH_BACKGROUND,
        "subtitle": SUBTITLE,
        "description": DESCRIPTION,
        "screenshotUrls": [f"{img}/screenshot-{i}.png" for i in (1, 2, 3)],
        "primaryCategory": PRIMARY_CATEGORY,
        "tags": TAGS,
        "heroImageUrl": f"{img}/hero-1200x630.png",
        "tagline": TAGLINE,
        "ogTitle": OG_TITLE,
        "ogDescription": OG_DESCRIPTION,
        "ogImageUrl": f"{img}/hero-1200x630.png",
        "noindex": not is_production(),
        "requiredChains": REQUIRED_CHAINS,
    }
    if notifications_enabled():
        # 宿主在使用者「加入／開通知」時把 token POST 到這裡（JFS 簽名，驗過才收）
        app["webhookUrl"] = f"{base}/api/miniapp/webhook"
    manifest: Dict[str, Any] = {
        "miniapp": app,
        "frame": app,
    }  # 新舊鍵都給，舊客戶端讀 frame
    assoc = account_association(urlparse(base).netloc.lower())
    if assoc:
        manifest["accountAssociation"] = assoc
    addrs = builder_addresses()
    if addrs:
        manifest["baseBuilder"] = {"allowedAddresses": addrs}
    return manifest


def embed_meta_content(base: str) -> str:
    """``<meta name="fc:miniapp" content="…">`` 的 content（JSON 字串）。"""
    base = base.rstrip("/")
    payload = {
        "version": "1",
        "imageUrl": f"{base}/img/miniapp/embed-1200x800.png",  # 3:2
        "button": {
            "title": BUTTON_TITLE,
            "action": {
                "type": "launch_miniapp",
                "name": APP_NAME,
                "url": f"{base}/",
                "splashImageUrl": f"{base}/img/miniapp/splash-200.png",
                "splashBackgroundColor": SPLASH_BACKGROUND,
            },
        },
    }
    return json.dumps(payload, separators=(",", ":"))


def embed_meta_tags(base: str) -> str:
    """首頁 <head> 要注入的兩個 meta（fc:miniapp 與舊名 fc:frame）。"""
    content = embed_meta_content(base).replace("&", "&amp;").replace('"', "&quot;")
    legacy = content.replace("launch_miniapp", "launch_frame")
    return (
        f'<meta name="fc:miniapp" content="{content}">\n'
        f'    <meta name="fc:frame" content="{legacy}">'
    )


def validate_manifest(manifest: Dict[str, Any]) -> List[str]:
    """規格上限守衛（測試用；也給 admin 看）。回問題清單，空＝OK。"""
    app = manifest.get("miniapp") or {}
    problems: List[str] = []
    if len(app.get("name", "")) > 32:
        problems.append("name > 32")
    if len(app.get("subtitle", "")) > 30:
        problems.append("subtitle > 30")
    if len(app.get("description", "")) > 170:
        problems.append("description > 170")
    if len(app.get("tagline", "")) > 30:
        problems.append("tagline > 30")
    if len(app.get("ogTitle", "")) > 30:
        problems.append("ogTitle > 30")
    if len(app.get("ogDescription", "")) > 100:
        problems.append("ogDescription > 100")
    if app.get("primaryCategory") not in VALID_CATEGORIES:
        problems.append("primaryCategory invalid")
    tags = app.get("tags") or []
    if len(tags) > 5 or any(len(t) > 20 for t in tags):
        problems.append("tags > 5 or tag > 20 chars")
    if len(app.get("screenshotUrls") or []) > 3:
        problems.append("screenshots > 3")
    for key in ("name", "homeUrl", "iconUrl", "version"):
        if not app.get(key):
            problems.append(f"{key} missing")
    return problems
