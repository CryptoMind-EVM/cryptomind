"""平台情境（web／tma／baseapp／play）：同一個後端、同一個帳號，入口不同能力不同。

設計：docs/plans/2026-09-13-google-play-twa-design.md §3。
- 前端每個請求帶 ``X-Platform``（platform-context.js）；沒帶＝web。
- 這份能力表是「這個入口看得到／做得到什麼」；**付款建單必須在後端用它擋**（Google 規定
  Play 版數位服務只能走 Play Billing，前端藏了但 API 還收等於沒擋）。
- 環境變數旗標（DAILY_BRIEF_ENABLED 之類）是「功能上不上線」，跟這裡是兩層。
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

WEB = "web"
TMA = "tma"
BASEAPP = "baseapp"
PLAY = "play"
PLATFORMS: FrozenSet[str] = frozenset({WEB, TMA, BASEAPP, PLAY})
DEFAULT = WEB

# 付款軌道 key 對齊 api/payment_rails（evm_usdc）＋ Play Billing
CAPABILITIES: Dict[str, Dict[str, bool]] = {
    WEB: {
        "wallet_login": True,
        "google_login": True,
        "telegram_login": False,
        "rail_evm_usdc": True,
        "rail_google_play": False,
        "crypto_tab": True,
    },
    TMA: {  # Telegram：App 內不收加密貨幣付款（tApps 規定只能 TON，TON 軌已移除）、Telegram 原生登入
        "wallet_login": False,
        "google_login": False,
        "telegram_login": True,
        "rail_evm_usdc": False,
        "rail_google_play": False,
        "crypto_tab": True,
    },
    BASEAPP: {
        "wallet_login": True,
        "google_login": False,
        "telegram_login": False,
        "rail_evm_usdc": True,
        "rail_google_play": False,
        "crypto_tab": True,
    },
    PLAY: {  # Google Play：數位服務只能走 Play Billing；錢包是之後可選綁定
        "wallet_login": True,
        "google_login": True,
        "telegram_login": False,
        "rail_evm_usdc": False,
        "rail_google_play": True,
        "crypto_tab": False,  # 市場分頁的加密貨幣子分頁：Play 版不放（政策風險），其餘入口開
    },
}


def normalize(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    return v if v in PLATFORMS else DEFAULT


def from_request(request) -> str:
    """``X-Platform`` header → 平台；沒帶或亂帶一律 web（最保守的是 web 能力最全，
    但 Play 版一定會帶 header，所以「沒帶」不會放行 Play 使用者去付 USDC——Play 版的
    前端根本沒有那個入口，而 Google 審的是 Play 版）。"""
    try:
        return normalize(request.headers.get("x-platform"))
    except Exception:  # noqa: BLE001
        return DEFAULT


def capabilities(platform: str) -> Dict[str, bool]:
    return dict(CAPABILITIES[normalize(platform)])


def rail_allowed(platform: str, rail: str) -> bool:
    return bool(CAPABILITIES[normalize(platform)].get(f"rail_{rail}", False))


def android_assetlinks_payload() -> list:
    """``/.well-known/assetlinks.json``。SHA-256 指紋格式 ``AA:BB:…``（大寫、冒號分隔），
    多把用逗號分開。"""
    import os

    package = os.getenv("ANDROID_PACKAGE_NAME", "").strip()
    fps = [
        f.strip().upper()
        for f in os.getenv("ANDROID_CERT_SHA256", "").split(",")
        if f.strip()
    ]
    if not package or not fps:
        return []
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package,
                "sha256_cert_fingerprints": fps,
            },
        }
    ]
