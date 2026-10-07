"""地址工具：TON friendly ↔ raw、EVM 格式、縮短顯示。純函式，無網路。"""

from __future__ import annotations

import base64
import re
from typing import Optional

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_RAW_TON_RE = re.compile(r"^(-1|0):[0-9a-fA-F]{64}$")
# friendly 第一個 byte：bounceable 0x11（EQ）／non-bounceable 0x51（UQ），testnet 再加 0x80（kQ／0Q）
_TON_FRIENDLY_TAGS = (0x11, 0x51, 0x91, 0xD1)


def is_evm_address(address: str) -> bool:
    return bool(_EVM_RE.match((address or "").strip()))


def _crc16(data: bytes) -> int:
    """CRC16-XMODEM（TON friendly 地址最後 2 bytes）。"""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def ton_friendly_to_raw(address: str) -> Optional[str]:
    """任何寫法的 TON 地址 → ``0:<64 hex>``（TonAPI 事件裡的 sender／recipient 用 raw 格式）。

    同一個錢包有 raw（``0:``／``-1:``）與 friendly（EQ／UQ／kQ／0Q，masterchain 是 Ef／Uf…）
    幾種寫法——比對、去重一律先轉這個。不是合法 TON 地址回 None。

    friendly = base64(url)(36 bytes)：tag(1) workchain(1) hash(32) crc16(2)。
    CRC 要驗：使用者輸入（加監測錢包）也走這裡，打錯字不能對到別的 hash。
    """
    a = (address if isinstance(address, str) else "").strip()
    if _RAW_TON_RE.match(a):
        wc, h = a.split(":")
        return f"{int(wc)}:{h.lower()}"
    if len(a) != 48:
        return None
    try:
        raw = base64.urlsafe_b64decode(a + "==")
    except (ValueError, TypeError):
        try:
            raw = base64.b64decode(a + "==")
        except (ValueError, TypeError):
            return None
    if len(raw) != 36 or raw[0] not in _TON_FRIENDLY_TAGS or raw[1] not in (0x00, 0xFF):
        return None
    if _crc16(raw[:34]) != int.from_bytes(raw[34:], "big"):
        return None
    workchain = 0 if raw[1] == 0x00 else -1
    return f"{workchain}:{raw[2:34].hex()}"


def ton_raw_to_friendly(
    address: str,
    *,
    bounceable: bool = False,
    testnet: bool = False,
    url_safe: bool = True,
) -> Optional[str]:
    """任何寫法的 TON 地址 → friendly；預設 non-bounceable（UQ…，錢包慣用的顯示寫法）。

    不是合法 TON 地址回 None。
    """
    raw = ton_friendly_to_raw(address)
    if not raw:
        return None
    wc, h = raw.split(":")
    tag = (0x11 if bounceable else 0x51) | (0x80 if testnet else 0)
    body = bytes([tag, 0xFF if wc == "-1" else 0x00]) + bytes.fromhex(h)
    data = body + _crc16(body).to_bytes(2, "big")
    return (base64.urlsafe_b64encode if url_safe else base64.b64encode)(data).decode()


def scam_address_key(address: str) -> str:
    """scam_reports.scam_wallet_address 的儲存寫法：TON 一律 raw，其他鏈照舊 upper()。

    舊資料連 TON friendly 也 upper() 存——base64 大小寫有意義，upper 後解不回來，
    同一個錢包的 EQ／UQ 也變成兩串。
    """
    a = (address if isinstance(address, str) else "").strip()
    return ton_friendly_to_raw(a) or a.upper()


def scam_address_lookup_keys(address: str) -> list[str]:
    """查 scam_reports 要比對的所有寫法：儲存寫法（第一個）＋ TON 舊資料 upper() 過的寫法。

    舊寫法從 raw 反推：bounceable／non-bounceable × mainnet／testnet × url-safe／標準
    base64，再加 raw 本身 upper()——舊 API 什麼寫法都收，檢舉人用哪種都要對得到。
    注意：upper() 過的 friendly 會同時對到其他大小寫不同的合法地址，只能拿來「找」，
    不能當成這筆舊資料就是這個帳戶的證據。
    """
    raw = ton_friendly_to_raw(address)
    if not raw:
        return [scam_address_key(address)]
    legacy = {raw.upper()}
    for bounceable in (True, False):
        for testnet in (False, True):
            for url_safe in (True, False):
                friendly = ton_raw_to_friendly(
                    raw, bounceable=bounceable, testnet=testnet, url_safe=url_safe
                )
                legacy.add(friendly.upper())
    legacy.discard(raw)
    return [raw, *sorted(legacy)]


def is_ton_address(address: str) -> bool:
    """任何合法寫法的 TON 地址（取代 ``startswith("EQ")`` 之類的前綴判斷）。"""
    return ton_friendly_to_raw(address) is not None


def same_ton_address(a: str, b: str) -> bool:
    ra, rb = ton_friendly_to_raw(a), ton_friendly_to_raw(b)
    return bool(ra and rb and ra == rb)


def evm_identity_address(user_id: str) -> Optional[str]:
    """EVM 登入的身份 ``evm_<地址>``（見 evm_login）→ 小寫地址；其他身份回 None。"""
    uid = user_id if isinstance(user_id, str) else ""
    if uid.startswith("evm_") and is_evm_address(uid[4:]):
        return uid[4:].lower()
    return None


def wallet_identity_address(user_id: str) -> Optional[str]:
    """user_id 本身就是錢包的身份 → 地址：EVM 登入（evm_…）或舊 TON 登入（user_id 即地址）。

    Telegram（tg_）、Google（g_）等非錢包身份回 None——它們有沒有錢包要看 user_wallets。
    """
    evm = evm_identity_address(user_id)
    if evm:
        return evm
    if isinstance(user_id, str) and is_ton_address(user_id):
        return user_id
    return None


def short_address(address: str, head: int = 6, tail: int = 4) -> str:
    a = (address if isinstance(address, str) else "").strip()
    if len(a) <= head + tail + 1:
        return a
    return f"{a[:head]}…{a[-tail:]}"
