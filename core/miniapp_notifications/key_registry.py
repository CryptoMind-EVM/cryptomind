"""Farcaster Key Registry（Optimism）：確認 app key 真的屬於那個 fid。

``keyDataOf(uint256 fid, bytes key) -> (uint8 state, uint32 keyType)``，state 1 = ADDED。
公開 RPC 免金鑰；查過的 (fid,key) 快取一小時。RPC 掛掉回 None（呼叫端決定要不要放行）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

KEY_REGISTRY = "0x00000000Fc1237824fb747aBDE0FF18990E59b7e"
_SELECTOR = "ac34cc5a"  # keccak("keyDataOf(uint256,bytes)")[:4]
_STATE_ADDED = 1
_CACHE_TTL = 3600
_cache: Dict[Tuple[int, str], Tuple[float, bool]] = {}


def rpc_url() -> str:
    return os.getenv(
        "FARCASTER_OPTIMISM_RPC_URL", "https://mainnet.optimism.io"
    ).strip()


def encode_call(fid: int, key_hex: str) -> str:
    key = bytes.fromhex(key_hex[2:] if key_hex.startswith("0x") else key_hex)
    padded = key + b"\x00" * (-len(key) % 32)
    enc = (
        fid.to_bytes(32, "big")
        + (0x40).to_bytes(32, "big")
        + len(key).to_bytes(32, "big")
        + padded
    )
    return "0x" + _SELECTOR + enc.hex()


def decode_state(result_hex: str) -> Optional[int]:
    raw = result_hex[2:] if result_hex.startswith("0x") else result_hex
    if len(raw) < 64:
        return None
    return int(raw[:64], 16)


def app_key_active(fid: int, key_hex: str, *, timeout: float = 8.0) -> Optional[bool]:
    """True／False＝鏈上答案；None＝查不到（RPC 壞）。"""
    ck = (int(fid), key_hex.lower())
    hit = _cache.get(ck)
    now = time.time()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    try:
        resp = httpx.post(
            rpc_url(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_call",
                "params": [
                    {"to": KEY_REGISTRY, "data": encode_call(fid, key_hex)},
                    "latest",
                ],
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("result")
        state = decode_state(result) if isinstance(result, str) else None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[miniapp] key registry lookup failed fid=%s: %s", fid, exc)
        return None
    if state is None:
        return None
    ok = state == _STATE_ADDED
    _cache[ck] = (now, ok)
    return ok


def clear_cache() -> None:
    _cache.clear()
