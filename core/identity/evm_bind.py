"""
EVM 地址綁定——讓 TON 使用者綁定 EVM 地址以啟用 Human Passport 訊號

問題
----
Human Passport Scorer API 只認 EVM 地址（0x...），但 CryptoMind 使用者只有
TON 地址（EQ...）。本模組補「綁定」流程：使用者用 EVM 錢包 personal_sign
證明擁有權，驗證通過才綁定（防綁別人地址蹭分）。

設計（對稱既有 ton_proof）
----
- nonce：HMAC stateless（沿用舊 ton_proof 的 _sign 模式），不需 Redis。
  payload = "{ts}.{rnd}.{hmac}"，自帶簽章自驗證，TTL 15 分鐘。
- 簽章驗證：eth_account.Account.recover_message 還原簽章者，嚴格比對 evm_address。
- 訊息格式：固定 prefix + nonce + domain（防跨站 + 防重放）。
- nonce 一次性：bind 成功後，用過的 nonce 進短期 blacklist（記 ts，TTL 內拒重放）。
  為了 stateless，blacklist 也用 HMAC 簽——但其實 personal_sign 的訊息含 nonce，
  只要 nonce TTL 短（15 分鐘），重放窗口極小。v1 接受這個窗口（同 ton_proof）。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from eth_account import Account
from eth_account.messages import encode_defunct

from core.config import TRUST_EVM_BIND_DOMAIN, TRUST_EVM_BIND_PAYLOAD_TTL


def _sign(data: bytes) -> str:
    """HMAC-SHA256 簽章（沿用舊 ton_proof 的模式，用 JWT_SECRET_KEY）。"""
    secret = os.getenv("JWT_SECRET_KEY", "").encode()
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def generate_evm_bind_payload() -> str:
    """產生一次性 nonce payload（stateless，自帶 HMAC 簽章）。

    回傳 "{ts}.{rnd}.{sig}"，使用者要把它放進 personal_sign 的訊息裡簽。
    """
    ts = int(time.time())
    rnd = os.urandom(8).hex()
    body = f"{ts}.{rnd}"
    return f"{body}.{_sign(body.encode())}"


def _check_payload(payload: str) -> bool:
    """驗證 payload 是我們發的 + 尚未過期。"""
    try:
        ts_str, _rnd, sig = payload.split(".")
    except (ValueError, AttributeError):
        return False
    body = f"{ts_str}.{_rnd}".encode()  # type: ignore[name-defined]
    if not hmac.compare_digest(sig, _sign(body)):
        return False
    try:
        ts = int(ts_str)
    except ValueError:
        return False
    return (time.time() - ts) <= TRUST_EVM_BIND_PAYLOAD_TTL


def build_sign_message(payload: str) -> str:
    """組 personal_sign 的訊息（使用者簽這段文字）。

    固定 prefix + nonce + domain，防跨站重放。
    """
    return (
        f"Bind EVM address to CryptoMind\n"
        f"Domain: {TRUST_EVM_BIND_DOMAIN}\n"
        f"Nonce: {payload}\n"
        f"This signature proves you own this EVM address."
    )


def verify_evm_signature(*, evm_address: str, signature: str, payload: str) -> bool:
    """驗證 EVM personal_sign 簽章。

    Args:
        evm_address: 宣稱擁有的 EVM 地址（0x...）。
        signature: personal_sign 產生的簽章（0x...）。
        payload: 我們發的 nonce payload（build_sign_message 用的那個）。

    Returns:
        True 若簽章確實由 evm_address 的私鑰簽出（且 payload 有效）。
    """
    # 1. payload 必須是我們發的且未過期
    if not _check_payload(payload):
        return False

    # 2. 組訊息並還原簽章者
    message = build_sign_message(payload)
    try:
        msg = encode_defunct(text=message)
        recovered = Account.recover_message(msg, signature=signature)
    except Exception:  # noqa: BLE001 — 任何簽章格式錯誤都當驗證失敗
        return False

    # 3. 嚴格比對（case-insensitive，EVM 地址 checksum 不影響比對）
    return recovered.lower() == evm_address.lower()


def normalize_evm_address(addr: str) -> Optional[str]:
    """正規化 EVM 地址。回 (0x + 40 hex) 或 None（不合法）。"""
    if not addr:
        return None
    a = addr.strip().lower()
    if not a.startswith("0x"):
        return None
    hex_part = a[2:]
    if len(hex_part) != 40:
        return None
    try:
        int(hex_part, 16)
    except ValueError:
        return None
    return "0x" + hex_part


__all__ = [
    "generate_evm_bind_payload",
    "build_sign_message",
    "verify_evm_signature",
    "normalize_evm_address",
]
