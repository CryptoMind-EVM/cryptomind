"""
EVM (SIWE) verification — multichain design Part B.

Two responsibilities:
1. Login ownership — the user signs a server-issued message with their EVM
   wallet (personal_sign / EIP-191); we recover the signer address with
   eth-account and require an exact match.
2. (Part C, later) payment — verify an on-chain USDC transfer on Base.

Stateless HMAC nonce payloads (the pattern of the removed TON ton_proof flow) signed with
the JWT secret (no Redis; multi-worker safe). Login/bind replay is idempotent
(the same address resolves to the same account; UNIQUE(chain, address) blocks
cross-user binding), so a TTL'd payload is sufficient.

Contract wallets (Safe / AA, EIP-1271) are explicitly rejected for MVP:
personal_sign from a contract wallet is signed by a possibly-unrelated owner
key, so without EIP-1271 verification we cannot prove ownership (design 決策點 5).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import defunct_hash_message, encode_defunct
from eth_utils import to_checksum_address
from eth_utils.exceptions import ValidationError
from fastapi import HTTPException

from api.erc6492_validator import UNIVERSAL_SIG_VALIDATOR_BYTECODE
from api.utils import logger
from core.config import EVM_RPC_URL, IS_TESTNET

EVM_NONCE_PAYLOAD_TTL = 10 * 60  # nonce valid for 10 minutes

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _secret() -> bytes:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Server configuration error: JWT_SECRET_KEY not set",
        )
    return secret.encode()


def _sign(data: bytes) -> str:
    return hmac.new(_secret(), data, hashlib.sha256).hexdigest()[:32]


def validate_evm_address(address: str) -> str:
    """Return the address lower-cased after format validation (0x + 40 hex)."""
    if not isinstance(address, str) or not _EVM_ADDRESS_RE.fullmatch(address):
        raise HTTPException(status_code=400, detail="Invalid EVM address format")
    return address.lower()


def _expected_domain() -> str:
    """Fallback origin（SITE_URL 的 host）——請求沒帶 Host 或 Host 不在
    ALLOWED_HOSTS 白名單時用。"""
    from api.public_base import expected_site_domain

    return expected_site_domain()


def _host_allowed(host: str) -> bool:
    """跟 TrustedHostMiddleware 同一份 ALLOWED_HOSTS；沒設（開發環境）視為全放行。"""
    allowed = [
        h.strip().lower()
        for h in os.getenv("ALLOWED_HOSTS", "").split(",")
        if h.strip()
    ]
    if not allowed:
        return True
    hostname = host.split(":", 1)[0]
    for pat in allowed:
        if pat in ("*", host, hostname):
            return True
        if pat.startswith("*.") and (hostname == pat[2:] or hostname.endswith(pat[1:])):
            return True
    return False


def resolve_siwe_domain(request_host: str | None) -> str:
    """SIWE 訊息的 domain（ERC-4361 authority）。

    2026-09-12 起優先用瀏覽器實際打到的 Host（錢包也是拿 window.location.host
    組訊息／顯示給使用者看），只有 Host 缺席或不在 ALLOWED_HOSTS 才退回
    TON manifest 的 host——原本一律用 manifest env，staging／自訂網域／本機帶
    port 的環境會對不上：One-Click 路徑直接 401，classic 路徑則是簽名視窗上
    的網域跟網址列不同。
    """
    host = (request_host or "").strip().rsplit("@", 1)[-1].rstrip(".").lower()
    if host and _host_allowed(host):
        return host
    return _expected_domain()


# ---------------------------------------------------------------------------
# 1. nonce payload — stateless, self-verifying
# ---------------------------------------------------------------------------


# ERC-4361 的 nonce 規格是 `nonce = 8*( ALPHA / DIGIT )`——不含點。One-Click
# Auth 由錢包自己組訊息，嚴格照規格實作的錢包會拒收帶點的 nonce，所以格式改成
# 純十六進位、固定寬度：<ts:8><rnd:16><sig:32>。
_NONCE_TS_HEX_LEN = 8
_NONCE_RND_HEX_LEN = 16
_NONCE_SIG_LEN = 32
_NONCE_BODY_LEN = _NONCE_TS_HEX_LEN + _NONCE_RND_HEX_LEN
_NONCE_TOTAL_LEN = _NONCE_BODY_LEN + _NONCE_SIG_LEN


def generate_evm_nonce_payload() -> str:
    """Issue a stateless, self-verifying nonce for the wallet to sign."""
    ts = int(time.time())
    body = f"{ts:0{_NONCE_TS_HEX_LEN}x}{os.urandom(8).hex()}"
    return f"{body}{_sign(body.encode())}"


def _split_nonce(payload: str) -> tuple[str, str, str]:
    """Return (ts_str, body, sig) for either nonce format, or raise 401.

    Legacy ``<ts>.<rnd>.<sig>`` payloads issued before the ERC-4361 change are
    still accepted so nonces already in users' hands don't expire mid-login.
    """
    if not isinstance(payload, str):
        raise HTTPException(status_code=401, detail="Invalid nonce payload")
    if "." in payload:
        try:
            ts_str, rnd, sig = payload.split(".")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid nonce payload")
        return ts_str, f"{ts_str}.{rnd}", sig
    if len(payload) != _NONCE_TOTAL_LEN:
        raise HTTPException(status_code=401, detail="Invalid nonce payload")
    body = payload[:_NONCE_BODY_LEN]
    return str(int(body[:_NONCE_TS_HEX_LEN], 16)), body, payload[_NONCE_BODY_LEN:]


def _check_payload(payload: str) -> int:
    """Validate a payload we previously issued. Returns its issue timestamp."""
    try:
        ts_str, body, sig = _split_nonce(payload)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid nonce payload")
    if not hmac.compare_digest(sig, _sign(body.encode())):
        raise HTTPException(status_code=401, detail="Invalid nonce payload")
    try:
        ts = int(ts_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid nonce payload")
    if (time.time() - ts) > EVM_NONCE_PAYLOAD_TTL:
        raise HTTPException(status_code=401, detail="Nonce expired — please retry")
    return ts


# --- nonce 用後即焚（2026-09-10 登入鏈徹查）----------------------------------
# HMAC+TTL 只證明「nonce 是我們發的、還沒過期」，不能證明「沒被用過」——
# 截獲的 (message, signature) 在 TTL 內可重放 evm-login（直接接管 session）
# 或 wallets/bind（把受害者地址歸戶到攻擊者帳號）。驗章當下把 nonce 燒掉，
# 重複出現即 401。In-memory（與 auth_failure_tracker 同模式）——跨 worker
# 的完整防護要接 Redis SET NX EX（GAP 同款），此為單 worker 內的即時收斂。
_used_nonces: dict[str, float] = {}  # payload hash -> 過期時刻（epoch 秒）
_USED_NONCES_MAX = 8192


_NONCE_REDIS_PREFIX = "stock_agent:siwe_nonce:"


def _nonce_store_client():
    """跨 worker 的 nonce 用後即焚需要共享儲存；拿不到 Redis 回 None（走 in-memory）。"""
    try:
        from core.database.cache import get_redis_client

        return get_redis_client()
    except Exception as exc:  # noqa: BLE001 — Redis 缺席不能擋登入
        logger.debug("nonce store: Redis unavailable (%s)", exc)
        return None


def _consume_nonce(payload: str) -> None:
    """Burn a validated nonce; raise 401 on replay within its TTL.

    2026-09-12：改以 Redis ``SET NX EX`` 為主——in-memory 集合每個 gunicorn
    worker 各一份，10 分鐘 TTL 內同一簽章可在別的 worker 重放。Redis 不可用
    時退回原本的 in-memory（單 worker 仍有效，多 worker 只剩機率性防護）。
    """
    key = hashlib.sha256(payload.encode()).hexdigest()
    now = time.time()
    client = _nonce_store_client()
    if client is not None:
        try:
            acquired = client.set(
                _NONCE_REDIS_PREFIX + key, "1", nx=True, ex=EVM_NONCE_PAYLOAD_TTL
            )
        except Exception as exc:  # noqa: BLE001 — 連線抖動退回 in-memory
            logger.warning(
                "nonce store: Redis write failed, in-memory fallback: %s", exc
            )
        else:
            if not acquired:
                raise HTTPException(
                    status_code=401, detail="Nonce already used — please retry"
                )
            return
    if len(_used_nonces) > _USED_NONCES_MAX:
        for stale_key in [k for k, exp in _used_nonces.items() if exp <= now]:
            _used_nonces.pop(stale_key, None)
        if len(_used_nonces) > _USED_NONCES_MAX:
            # 仍超量（極端流量）：丟最舊的，防記憶體無界成長
            oldest = min(_used_nonces, key=_used_nonces.get)
            _used_nonces.pop(oldest, None)
    if _used_nonces.get(key, 0.0) > now:
        raise HTTPException(status_code=401, detail="Nonce already used — please retry")
    _used_nonces[key] = now + EVM_NONCE_PAYLOAD_TTL


# ---------------------------------------------------------------------------
# 2. SIWE message + signature recovery
# ---------------------------------------------------------------------------


SIWE_STATEMENT = "Sign in to CryptoMind"
# Base mainnet／Base Sepolia 隨 NETWORK 切（與 EVM_RPC_URL 同源）
SIWE_CHAIN_ID = 84532 if IS_TESTNET else 8453


def _siwe_uri(domain: str) -> str:
    host = domain.split(":")[0]
    scheme = "http" if host in ("localhost", "127.0.0.1") else "https"
    return f"{scheme}://{domain}"


def build_siwe_message(
    address: str, payload: str, issued_ts: int, domain: str | None = None
) -> str:
    """Deterministic ERC-4361 message the wallet must sign (client signs it verbatim).

    2026-09-13：舊格式第一行像 SIWE、其餘欄位卻是自家的（Domain:／Issued:），MetaMask 只是
    不管，Base App／Coinbase Wallet 的 provider 會照 EIP-4361 嚴格解析 → personal_sign
    直接回「Invalid message」，mini app 裡完全登不進。改成標準欄位；nonce 是我們簽過的
    hex（字母數字 ≥8，符合規範），Issued At 由 nonce 裡的時間戳推出，驗章時重建一致。
    """
    domain = domain or _expected_domain()
    checksum = to_checksum_address(address)
    issued_at = datetime.fromtimestamp(int(issued_ts), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{checksum}\n"
        "\n"
        f"{SIWE_STATEMENT}\n"
        "\n"
        f"URI: {_siwe_uri(domain)}\n"
        "Version: 1\n"
        f"Chain ID: {SIWE_CHAIN_ID}\n"
        f"Nonce: {payload}\n"
        f"Issued At: {issued_at}"
    )


def issue_siwe_challenge(
    address: str | None, domain: str | None = None
) -> Dict[str, Any]:
    """Issue a login challenge: nonce payload + the exact message to sign.

    ``address`` is optional: One-Click Auth asks for a nonce before the wallet
    has been approved, so there is no address yet. Without one we issue the
    nonce alone — the wallet composes the ERC-4361 message itself and the
    nonce is what anchors our verification either way.
    """
    addr_lower = validate_evm_address(address) if address is not None else None
    payload = generate_evm_nonce_payload()
    issued_ts = _check_payload(payload)
    return {
        "address": addr_lower,
        "nonce_token": payload,
        "message": (
            build_siwe_message(addr_lower, payload, issued_ts, domain)
            if addr_lower
            else None
        ),
        "expires_in": EVM_NONCE_PAYLOAD_TTL,
    }


def _prepare_siwe_recovery(
    address: str, payload: str, domain: str | None = None
) -> tuple[str, str]:
    """驗 nonce、用後即焚、重建要比對的訊息。classic 路徑 sync／async 共用。"""
    addr_lower = validate_evm_address(address)
    issued_ts = _check_payload(payload)
    _consume_nonce(payload)  # 用後即焚：重放防護（見 _consume_nonce 註記）
    return addr_lower, build_siwe_message(addr_lower, payload, issued_ts, domain)


def _recover_eoa_signer(message: str, signature: str) -> str:
    """ecrecover；解不開一律 401（不洩漏細節）。回傳小寫地址。"""
    try:
        signable = encode_defunct(text=message)
        recovered = Account.recover_message(signable, signature=signature)
    except (
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ) as exc:
        logger.warning("SIWE signature decode failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid wallet signature")
    return (recovered or "").lower()


def recover_siwe_signer(
    *, address: str, payload: str, signature: str, domain: str | None = None
) -> str:
    """Verify the nonce payload and recover the signature's signer address (EOA only).

    Returns the recovered address lower-cased. Raises HTTPException on any
    failure (bad payload, malformed signature, signer mismatch). 合約錢包請走
    ``recover_siwe_signer_async``。
    """
    addr_lower, message = _prepare_siwe_recovery(address, payload, domain)
    recovered_lower = _recover_eoa_signer(message, signature)
    if recovered_lower != addr_lower:
        logger.warning(
            "SIWE signer mismatch: recovered=%s claimed=%s",
            recovered_lower[:10],
            addr_lower[:10],
        )
        raise HTTPException(status_code=401, detail="Signature does not match address")
    return recovered_lower


async def recover_siwe_signer_async(
    *, address: str, payload: str, signature: str, domain: str | None = None
) -> str:
    """classic 路徑的完整版：EOA ecrecover → 合約錢包 EIP-1271 → 未部署 EIP-6492。"""
    addr_lower, message = _prepare_siwe_recovery(address, payload, domain)
    return await verify_evm_signature(addr_lower, message, signature)


# ---------------------------------------------------------------------------
# 2a. 合約錢包（EIP-1271）與未部署合約錢包（EIP-6492）
# ---------------------------------------------------------------------------
#
# 2026-09-12 DANNY 拍板：Coinbase Smart Wallet（Base 上新手最常用，passkey、
# counterfactual 部署）、Safe 等合約錢包以前一律 403。personal_sign 對這些錢包
# 回來的不是 65 bytes 的 ecrecover 簽章，而是錢包自訂格式（Coinbase 是
# SignatureWrapper，未部署時再包一層 ERC-6492 信封）。驗法：
#   1. 65 bytes 且 ecrecover 等於宣稱地址 → EOA，零 RPC（原路徑不變）
#   2. 帶 ERC-6492 magic 後綴 → deployless 通用驗證器一次 eth_call 搞定
#      （未部署就先透過 factory 部署在暫態內，再呼叫 isValidSignature）
#   3. 其餘：地址有 code → 直接呼叫合約的 isValidSignature(bytes32,bytes)
#      （EIP-1271，回 0x1626ba7e 即有效）；沒 code → 就是簽錯的 EOA，401
# RPC 打不到時合約路徑 502（fail-closed）；EOA 路徑不受影響。

_ERC6492_MAGIC = bytes.fromhex("6492" * 32)
_ERC1271_MAGIC = "0x1626ba7e"


def _signature_bytes(signature: str) -> bytes:
    try:
        return bytes.fromhex(str(signature).removeprefix("0x"))
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid wallet signature")


def _is_erc6492(sig: bytes) -> bool:
    return len(sig) > 32 and sig.endswith(_ERC6492_MAGIC)


async def _rpc(method: str, params: list) -> Any:
    """JSON-RPC；網路／閘道錯誤 502，RPC 層 error（如 revert）回 None。"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                EVM_RPC_URL,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
    except httpx.HTTPError as exc:
        logger.warning("EVM RPC %s failed: %s", method, exc)
        raise HTTPException(
            status_code=502,
            detail="Unable to reach the EVM network to verify the signature",
        )
    if resp.status_code != 200:
        logger.warning("EVM RPC %s status %s", method, resp.status_code)
        raise HTTPException(
            status_code=502,
            detail="Unable to reach the EVM network to verify the signature",
        )
    try:
        body = resp.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Unable to reach the EVM network to verify the signature",
        )
    if not isinstance(body, dict) or "result" not in body:
        return None
    return body.get("result")


async def _eth_get_code(address: str) -> str:
    return (await _rpc("eth_getCode", [address, "latest"])) or "0x"


async def _validate_erc1271(address: str, msg_hash: bytes, sig: bytes) -> bool:
    data = _ERC1271_MAGIC + abi_encode(["bytes32", "bytes"], [msg_hash, sig]).hex()
    result = await _rpc("eth_call", [{"to": address, "data": data}, "latest"])
    return isinstance(result, str) and result.lower().startswith(_ERC1271_MAGIC)


async def _validate_erc6492(address: str, msg_hash: bytes, sig: bytes) -> bool:
    args = abi_encode(
        ["address", "bytes32", "bytes"], [to_checksum_address(address), msg_hash, sig]
    ).hex()
    result = await _rpc(
        "eth_call", [{"data": UNIVERSAL_SIG_VALIDATOR_BYTECODE + args}, "latest"]
    )
    if not isinstance(result, str) or len(result) < 4:
        return False
    try:
        return int(result, 16) == 1
    except ValueError:
        return False


async def verify_evm_signature(addr_lower: str, message: str, signature: str) -> str:
    """驗 ``message`` 的簽章屬於 ``addr_lower``（EOA／EIP-1271／EIP-6492）。回傳小寫地址。"""
    sig = _signature_bytes(signature)
    if len(sig) == 65:
        try:
            if _recover_eoa_signer(message, signature) == addr_lower:
                return addr_lower
        except HTTPException:
            pass  # 解不開的 65 bytes：可能是合約錢包的自訂格式，往下試
    msg_hash = bytes(defunct_hash_message(text=message))
    if _is_erc6492(sig):
        kind = "erc6492"
        ok = await _validate_erc6492(addr_lower, msg_hash, sig)
    else:
        code = await _eth_get_code(addr_lower)
        if not isinstance(code, str) or len(code) <= 2:
            logger.warning("SIWE signer mismatch for EOA %s", addr_lower[:10])
            raise HTTPException(
                status_code=401, detail="Signature does not match address"
            )
        kind = "erc1271"
        ok = await _validate_erc1271(addr_lower, msg_hash, sig)
    if not ok:
        logger.warning("SIWE %s validation failed for %s", kind, addr_lower[:10])
        raise HTTPException(status_code=401, detail="Signature does not match address")
    logger.info("SIWE %s signature accepted for %s", kind, addr_lower[:10])
    return addr_lower


# ---------------------------------------------------------------------------
# 2b. One-Click Auth — 錢包自己組的 ERC-4361 訊息
# ---------------------------------------------------------------------------
#
# WalletConnect 的 One-Click Auth（wallet_authenticate）把「連線」與「登入簽名」
# 併成一次錢包往返：我們只給 nonce / domain / uri 等欄位，最終的 ERC-4361 字串
# 由錢包組。所以不能再用 nonce_token 重建訊息比對——必須驗證使用者送上來的
# message 本身。信任錨仍然是那顆 stateless HMAC nonce：訊息裡的每個欄位都可能
# 被竄改，但 nonce 不是我們簽的、或過期，就一律拒絕。

# 保守上限：正常 SIWE 訊息不到 1KB，超過視為攻擊面而非合法輸入
_SIWE_MESSAGE_MAX_LEN = 4096
_SIWE_FIRST_LINE_RE = re.compile(
    r"^(?:\w+://)?(?P<domain>[^\s/]+) wants you to sign in with your Ethereum account:$"
)
_SIWE_FIELD_RE = {
    "nonce": re.compile(r"^Nonce: (?P<v>\S+)$", re.MULTILINE),
    "expiration": re.compile(r"^Expiration Time: (?P<v>\S+)$", re.MULTILINE),
    "not_before": re.compile(r"^Not Before: (?P<v>\S+)$", re.MULTILINE),
}


def _parse_iso8601(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _reject(reason: str) -> None:
    """Log the real reason, tell the client nothing beyond 'invalid'."""
    logger.warning("SIWE wallet message rejected: %s", reason)
    raise HTTPException(status_code=401, detail="Invalid wallet signature")


def _prepare_wallet_message(
    address: str, message: str, expected_domain: str | None = None
) -> str:
    """One-Click 訊息的所有非簽章檢查（格式／domain／nonce 用後即焚／時效／地址）。"""
    addr_lower = validate_evm_address(address)
    expected = expected_domain or _expected_domain()

    if not isinstance(message, str) or not message:
        _reject("empty message")
    if len(message) > _SIWE_MESSAGE_MAX_LEN:
        _reject("message too long")

    lines = message.split("\n")
    if len(lines) < 2:
        _reject("message too short")

    first = _SIWE_FIRST_LINE_RE.match(lines[0].strip())
    if not first:
        _reject("first line is not ERC-4361")
    domain = first.group("domain").rstrip(".").lower()
    if domain != expected:
        _reject(f"domain mismatch: {domain}")

    nonce_match = _SIWE_FIELD_RE["nonce"].search(message)
    if not nonce_match:
        _reject("no Nonce field")
    # 這一步是整段驗證的信任錨：nonce 不是我們簽的、或已過期就直接出局
    _check_payload(nonce_match.group("v"))
    _consume_nonce(nonce_match.group("v"))  # One-Click 的信任錨同樣用後即焚

    now = datetime.now(timezone.utc)
    exp_match = _SIWE_FIELD_RE["expiration"].search(message)
    if exp_match:
        expires_at = _parse_iso8601(exp_match.group("v"))
        if expires_at is None or expires_at <= now:
            _reject("message expired")
    nbf_match = _SIWE_FIELD_RE["not_before"].search(message)
    if nbf_match:
        not_before = _parse_iso8601(nbf_match.group("v"))
        if not_before is None or not_before > now:
            _reject("message not yet valid")

    if lines[1].strip().lower() != addr_lower:
        _reject("address line does not match claimed address")
    return addr_lower


async def verify_siwe_wallet_message_async(
    *, address: str, message: str, signature: str, expected_domain: str | None = None
) -> str:
    """One-Click 訊息的完整版：非簽章檢查 + EOA／EIP-1271／EIP-6492 驗章。"""
    addr_lower = _prepare_wallet_message(address, message, expected_domain)
    try:
        return await verify_evm_signature(addr_lower, message, signature)
    except HTTPException as exc:
        if exc.status_code == 401:
            _reject(f"signature invalid for {addr_lower[:10]}")
        raise


def verify_siwe_wallet_message(
    *, address: str, message: str, signature: str, expected_domain: str | None = None
) -> str:
    """Verify a wallet-composed ERC-4361 message and return the signer address (EOA only).

    Checks, in order (all must hold):
    1. message is a plausible ERC-4361 string
    2. its domain is this deployment's origin  → blocks a signature farmed on
       another site being replayed here
    3. its Nonce is one we issued and still fresh (HMAC + TTL) → blocks replay
       and wallet-invented nonces
    4. Expiration Time / Not Before, when present, are currently valid
    5. the address it claims matches the claimed address
    6. the signature recovers to that same address
    """
    addr_lower = _prepare_wallet_message(address, message, expected_domain)
    try:
        signable = encode_defunct(text=message)
        recovered = Account.recover_message(signable, signature=signature)
    except (ValueError, TypeError, KeyError, ValidationError) as exc:
        _reject(f"signature decode failed: {exc}")

    recovered_lower = (recovered or "").lower()
    if recovered_lower != addr_lower:
        _reject(
            f"signer mismatch: recovered={recovered_lower[:10]} claimed={addr_lower[:10]}"
        )
    return recovered_lower


# ---------------------------------------------------------------------------
# 3. (Part C) payment order binding — generalized HMAC order token
# ---------------------------------------------------------------------------


def create_multichain_order(
    user_id: str,
    plan: str,
    rail: str,
    fiat_amount_usd: float,
    quoted_amount: float | None = None,
    rate: float | None = None,
    *,
    ttl_seconds: int = 30 * 60,
    extra: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """HMAC-signed order token binding user+plan+rail+amount (design §C).

    ``extra`` carries rail-specific fields (receiving address, memo/comment).
    The token is the single source of truth at upgrade time — the client can
    tamper with nothing, including the rail.
    """
    exp = int(time.time()) + ttl_seconds
    payload: Dict[str, Any] = {
        "u": user_id,
        "p": plan,
        "rail": rail,
        "usd": round(float(fiat_amount_usd), 4),
        "e": exp,
        # order issue time — stable-rail verification rejects transfers that
        # predate the order (anti "pre-mined order claims an observed transfer")
        "iat": int(time.time()),
    }
    if quoted_amount is not None:
        payload["a"] = round(float(quoted_amount), 9)
    if rate is not None:
        payload["rate"] = round(float(rate), 10)
    if extra:
        payload.update(extra)
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    token = base64.urlsafe_b64encode(raw).decode() + "." + _sign(raw)
    return {"order_token": token, "expires_at": exp, "payload": payload}


def verify_multichain_order_token(order_token: str, user_id: str) -> Dict[str, Any]:
    """Verify and decode an order token, ensuring it belongs to ``user_id``."""
    try:
        b64, sig = order_token.split(".")
        raw = base64.urlsafe_b64decode(b64)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid order token")
    if not hmac.compare_digest(sig, _sign(raw)):
        raise HTTPException(status_code=400, detail="Order token signature mismatch")
    payload = json.loads(raw)
    if payload.get("u") != user_id:
        raise HTTPException(status_code=403, detail="Order does not belong to user")
    if int(payload.get("e", 0)) < int(time.time()):
        raise HTTPException(status_code=400, detail="Order has expired")
    return payload
