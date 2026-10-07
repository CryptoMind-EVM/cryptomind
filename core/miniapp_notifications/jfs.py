"""JSON Farcaster Signature（JFS）驗證——webhook 事件的信封。

信封：``{"header": b64url(JSON{fid,type,key}), "payload": b64url(JSON), "signature": b64url(64 bytes)}``
簽的是 UTF-8 的 ``header + "." + payload``（兩段 base64url 字串本身），Ed25519，公鑰在 header.key。
對齊 @farcaster/miniapp-node 的 verifyJsonFarcasterSignature；type 只收 ``app_key``。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class JfsError(ValueError):
    """信封壞掉／簽名不對／app key 不屬於該 fid。"""


@dataclass(frozen=True)
class VerifiedEvent:
    fid: int
    app_key: str
    payload: Dict[str, Any]


def _b64url_decode(s: str) -> bytes:
    if not isinstance(s, str) or not s:
        raise JfsError("empty segment")
    pad = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception as exc:  # noqa: BLE001
        raise JfsError("bad base64url") from exc


def verify_jfs(body: Any, verify_app_key: Callable[[int, str], bool]) -> VerifiedEvent:
    """驗簽並回傳 (fid, app_key, payload)。``verify_app_key(fid, key_hex)`` 由呼叫端提供
    （Key Registry 或 Neynar）；回 False 就當假事件。"""
    if not isinstance(body, dict):
        raise JfsError("body must be an object")
    header_b64 = body.get("header")
    payload_b64 = body.get("payload")
    signature_b64 = body.get("signature")
    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JfsError("bad header") from exc
    if not isinstance(header, dict) or header.get("type") != "app_key":
        raise JfsError("header.type must be app_key")
    fid = header.get("fid")
    key = header.get("key")
    if not isinstance(fid, int) or fid <= 0:
        raise JfsError("header.fid invalid")
    if not isinstance(key, str) or not key.startswith("0x") or len(key) != 66:
        raise JfsError("header.key invalid")
    signature = _b64url_decode(signature_b64)
    if len(signature) != 64:
        raise JfsError("signature length")
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key[2:]))
        pub.verify(signature, f"{header_b64}.{payload_b64}".encode("utf-8"))
    except (ValueError, InvalidSignature) as exc:
        raise JfsError("invalid signature") from exc
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JfsError("bad payload") from exc
    if not isinstance(payload, dict):
        raise JfsError("payload must be an object")
    if not verify_app_key(fid, key):
        raise JfsError("app key not valid for fid")
    return VerifiedEvent(fid=fid, app_key=key.lower(), payload=payload)
