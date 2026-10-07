"""Unit tests for EVM SIWE verification (login proof) — multichain design Part B."""

import os
import time
from unittest.mock import patch

import pytest
from eth_account import Account
from fastapi import HTTPException

import api.evm_verification as ev


@pytest.fixture
def wallet():
    """Random EOA keypair (address lower-cased)."""
    acct = Account.create()
    return acct.address.lower(), acct


def _sign_message(acct, message: str) -> str:
    signable = ev.encode_defunct(text=message)
    signed = acct.sign_message(signable)
    return (
        signed["signature"].hex()
        if isinstance(signed, dict)
        else signed.signature.hex()
    )


# --- nonce payload -----------------------------------------------------------


@pytest.mark.unit
def test_nonce_payload_roundtrip_valid():
    p = ev.generate_evm_nonce_payload()
    assert ev._check_payload(p) <= int(time.time())


@pytest.mark.unit
def test_nonce_payload_tampered_rejected():
    p = ev.generate_evm_nonce_payload()
    tampered = p[:-1] + ("0" if p[-1] != "0" else "1")
    with pytest.raises(HTTPException) as exc:
        ev._check_payload(tampered)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_nonce_payload_expired_rejected(monkeypatch):
    p = ev.generate_evm_nonce_payload()
    future = time.time() + ev.EVM_NONCE_PAYLOAD_TTL + 10
    monkeypatch.setattr(ev.time, "time", lambda: future)
    with pytest.raises(HTTPException) as exc:
        ev._check_payload(p)
    assert exc.value.status_code == 401
    assert "expired" in str(exc.value.detail).lower()


# --- address validation ------------------------------------------------------


@pytest.mark.unit
def test_validate_address_rejects_non_hex():
    with pytest.raises(HTTPException) as exc:
        ev.validate_evm_address("0xzz00000000000000000000000000000000000000")
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_validate_address_rejects_wrong_length():
    with pytest.raises(HTTPException) as exc:
        ev.validate_evm_address("0x1234")
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_validate_address_lower_cases_checksum_input():
    checksum = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    assert ev.validate_evm_address(checksum) == checksum.lower()


# --- SIWE signature ----------------------------------------------------------


@pytest.mark.unit
def test_verify_siwe_valid(wallet):
    addr, acct = wallet
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    message = ev.build_siwe_message(addr, payload, ts)
    signature = _sign_message(acct, message)
    recovered = ev.recover_siwe_signer(
        address=addr, payload=payload, signature=signature
    )
    assert recovered == addr


@pytest.mark.unit
def test_verify_siwe_checksum_address_input_valid(wallet):
    addr, acct = wallet
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    message = ev.build_siwe_message(addr, payload, ts)
    signature = _sign_message(acct, message)
    recovered = ev.recover_siwe_signer(
        address=ev.to_checksum_address(addr), payload=payload, signature=signature
    )
    assert recovered == addr


@pytest.mark.unit
def test_verify_siwe_wrong_key_rejected(wallet):
    addr, acct = wallet
    impostor = Account.create()
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    message = ev.build_siwe_message(addr, payload, ts)
    signature = _sign_message(impostor, message)
    with pytest.raises(HTTPException) as exc:
        ev.recover_siwe_signer(address=addr, payload=payload, signature=signature)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_siwe_tampered_message_rejected(wallet):
    addr, acct = wallet
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    message = ev.build_siwe_message(addr, payload, ts)
    signature = _sign_message(acct, message)
    # a payload from a different nonce invalidates the signed message
    other_payload = ev.generate_evm_nonce_payload()
    with pytest.raises(HTTPException):
        ev.recover_siwe_signer(address=addr, payload=other_payload, signature=signature)
    # a different issued-ts line invalidates it too
    forged = ev.build_siwe_message(addr, payload, ts + 1)
    signable = ev.encode_defunct(text=forged)
    bad_sig = (
        acct.sign_message(signable)["signature"].hex()
        if isinstance(acct.sign_message(signable), dict)
        else acct.sign_message(signable).signature.hex()
    )
    with pytest.raises(HTTPException):
        ev.recover_siwe_signer(address=addr, payload=payload, signature=bad_sig)
    assert signature != bad_sig


@pytest.mark.unit
def test_verify_siwe_malformed_signature_rejected(wallet):
    addr, _ = wallet
    payload = ev.generate_evm_nonce_payload()
    with pytest.raises(HTTPException):
        ev.recover_siwe_signer(address=addr, payload=payload, signature="0xdeadbeef")


@pytest.mark.unit
def test_verify_siwe_cross_domain_message_rejected(wallet, monkeypatch):
    """A signature made for another origin must not verify here."""
    addr, acct = wallet
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    real_build = ev.build_siwe_message

    def _foreign_build(address, p, issued):
        good = real_build(address, p, issued)
        dom = ev._expected_domain()
        return good.replace(
            f"{dom} wants you to sign in", "evil.example.com wants you to sign in"
        ).replace(f"URI: {ev._siwe_uri(dom)}", "URI: https://evil.example.com")

    monkeypatch.setattr(ev, "build_siwe_message", _foreign_build)
    message = _foreign_build(addr, payload, ts)
    signature = _sign_message(acct, message)
    monkeypatch.setattr(ev, "build_siwe_message", real_build)
    with pytest.raises(HTTPException) as exc:
        ev.recover_siwe_signer(address=addr, payload=payload, signature=signature)
    assert exc.value.status_code == 401


# --- 合約錢包：EIP-1271 / EIP-6492（2026-09-12 取代原本的一律 403）-------------


def _fake_rpc(handlers):
    """以 method 分派的假 JSON-RPC；handlers[method] 是 callable(params) -> result。"""
    calls = []

    async def rpc(method, params):
        calls.append((method, params))
        handler = handlers.get(method)
        if handler is None:
            raise AssertionError(f"unexpected rpc {method}")
        return handler(params)

    return rpc, calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_eoa_fast_path_needs_no_rpc(wallet):
    addr, acct = wallet
    message = "hello"
    sig = _sign_message(acct, message)
    rpc, calls = _fake_rpc({})
    with patch.object(ev, "_rpc", new=rpc):
        assert await ev.verify_evm_signature(addr, message, sig) == addr
    assert calls == [], "EOA 路徑不該打 RPC"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_eoa_mismatch_is_401(wallet):
    addr, _ = wallet
    other = Account.create()
    sig = _sign_message(other, "hello")
    rpc, calls = _fake_rpc({"eth_getCode": lambda p: "0x"})
    with patch.object(ev, "_rpc", new=rpc):
        with pytest.raises(HTTPException) as exc:
            await ev.verify_evm_signature(addr, "hello", sig)
    assert exc.value.status_code == 401
    assert [c[0] for c in calls] == ["eth_getCode"], "簽錯的 EOA 只需查一次 code"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_erc1271_deployed_contract(wallet):
    addr, _ = wallet
    sig = "0x" + "ab" * 100  # 合約錢包自訂格式，不是 65 bytes
    rpc, calls = _fake_rpc(
        {
            "eth_getCode": lambda p: "0x6080604052",
            "eth_call": lambda p: ev._ERC1271_MAGIC + "0" * 56,
        }
    )
    with patch.object(ev, "_rpc", new=rpc):
        assert await ev.verify_evm_signature(addr, "hello", sig) == addr
    call = [c for c in calls if c[0] == "eth_call"][0]
    assert call[1][0]["to"] == addr
    assert call[1][0]["data"].startswith(ev._ERC1271_MAGIC), (
        "要呼叫 isValidSignature(bytes32,bytes)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_erc1271_rejects_non_magic(wallet):
    addr, _ = wallet
    rpc, _ = _fake_rpc(
        {"eth_getCode": lambda p: "0x6080604052", "eth_call": lambda p: "0x" + "0" * 64}
    )
    with patch.object(ev, "_rpc", new=rpc):
        with pytest.raises(HTTPException) as exc:
            await ev.verify_evm_signature(addr, "hello", "0x" + "ab" * 100)
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_erc6492_counterfactual_wallet(wallet):
    """未部署的 Coinbase Smart Wallet：簽章帶 6492 後綴 → deployless 通用驗證器。"""
    addr, _ = wallet
    sig = "0x" + "cd" * 120 + ev._ERC6492_MAGIC.hex()
    rpc, calls = _fake_rpc({"eth_call": lambda p: "0x" + "0" * 63 + "1"})
    with patch.object(ev, "_rpc", new=rpc):
        assert await ev.verify_evm_signature(addr, "hello", sig) == addr
    assert len(calls) == 1 and calls[0][0] == "eth_call"
    call_obj = calls[0][1][0]
    assert "to" not in call_obj, "deployless：eth_call 不帶 to"
    assert call_obj["data"].startswith(ev.UNIVERSAL_SIG_VALIDATOR_BYTECODE)
    # ABI 的 bytes 補零到 32 bytes 邊界，magic 後綴在尾端補零之前
    assert ev._ERC6492_MAGIC.hex() in call_obj["data"], "整個含後綴的簽章都要餵給驗證器"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_verify_evm_signature_erc6492_rejected_when_validator_says_no(wallet):
    addr, _ = wallet
    sig = "0x" + "cd" * 120 + ev._ERC6492_MAGIC.hex()
    rpc, _ = _fake_rpc({"eth_call": lambda p: "0x" + "0" * 64})
    with patch.object(ev, "_rpc", new=rpc):
        with pytest.raises(HTTPException) as exc:
            await ev.verify_evm_signature(addr, "hello", sig)
    assert exc.value.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_contract_path_rpc_outage_is_502_fail_closed(wallet):
    addr, _ = wallet

    async def boom(method, params):
        raise HTTPException(status_code=502, detail="down")

    with patch.object(ev, "_rpc", new=boom):
        with pytest.raises(HTTPException) as exc:
            await ev.verify_evm_signature(addr, "hello", "0x" + "ab" * 100)
    assert exc.value.status_code == 502


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recover_siwe_signer_async_eoa_roundtrip(wallet):
    addr, acct = wallet
    challenge = ev.issue_siwe_challenge(addr, domain="localhost:8080")
    sig = _sign_message(acct, challenge["message"])
    rpc, calls = _fake_rpc({})
    with patch.object(ev, "_rpc", new=rpc):
        recovered = await ev.recover_siwe_signer_async(
            address=addr,
            payload=challenge["nonce_token"],
            signature=sig,
            domain="localhost:8080",
        )
    assert recovered == addr and calls == []


@pytest.mark.unit
def test_domain_mismatch_between_nonce_and_login_rejected(wallet):
    """nonce 用 host A 發、登入用 host B 驗：訊息重建不同 → 401（這是安全性質）。"""
    addr, acct = wallet
    challenge = ev.issue_siwe_challenge(addr, domain="a.example")
    sig = _sign_message(acct, challenge["message"])
    with pytest.raises(HTTPException) as exc:
        ev.recover_siwe_signer(
            address=addr,
            payload=challenge["nonce_token"],
            signature=sig,
            domain="b.example",
        )
    assert exc.value.status_code == 401


# --- SIWE domain：請求 Host 優先 ----------------------------------------------


@pytest.mark.unit
def test_resolve_siwe_domain_prefers_request_host(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    assert ev.resolve_siwe_domain("Localhost:8080") == "localhost:8080"
    assert (
        ev.resolve_siwe_domain("user@Cryptomind-ton.zeabur.app.")
        == "cryptomind-ton.zeabur.app"
    )


@pytest.mark.unit
def test_resolve_siwe_domain_falls_back_when_host_not_allowed(monkeypatch):
    monkeypatch.setenv("ALLOWED_HOSTS", "cryptomind-ton.zeabur.app,*.preview.app")
    with patch.object(ev, "_expected_domain", return_value="manifest.host"):
        assert ev.resolve_siwe_domain("evil.example") == "manifest.host"
        assert ev.resolve_siwe_domain(None) == "manifest.host"
        assert (
            ev.resolve_siwe_domain("cryptomind-ton.zeabur.app")
            == "cryptomind-ton.zeabur.app"
        )
        assert ev.resolve_siwe_domain("pr-12.preview.app") == "pr-12.preview.app"


# --- nonce 用後即焚：Redis SETNX 跨 worker --------------------------------------


class _FakeRedis:
    def __init__(self, fail=False):
        self.keys = set()
        self.fail = fail
        self.calls = []

    def set(self, key, value, nx=False, ex=None):
        self.calls.append((key, nx, ex))
        if self.fail:
            raise ConnectionError("redis down")
        if key in self.keys:
            return None
        self.keys.add(key)
        return True


@pytest.mark.unit
def test_consume_nonce_uses_redis_setnx_with_ttl():
    fake = _FakeRedis()
    payload = ev.generate_evm_nonce_payload()
    with patch.object(ev, "_nonce_store_client", return_value=fake):
        ev._consume_nonce(payload)
        with pytest.raises(HTTPException) as exc:
            ev._consume_nonce(payload)
    assert exc.value.status_code == 401
    assert fake.calls[0][1] is True and fake.calls[0][2] == ev.EVM_NONCE_PAYLOAD_TTL
    assert fake.calls[0][0].startswith(ev._NONCE_REDIS_PREFIX)


@pytest.mark.unit
def test_consume_nonce_falls_back_to_memory_when_redis_fails():
    fake = _FakeRedis(fail=True)
    payload = ev.generate_evm_nonce_payload()
    with patch.object(ev, "_nonce_store_client", return_value=fake):
        ev._consume_nonce(payload)  # Redis 炸掉：走 in-memory，第一次要過
        with pytest.raises(HTTPException):
            ev._consume_nonce(payload)  # in-memory 仍擋重放


# --- multichain order token --------------------------------------------------


@pytest.mark.unit
def test_order_token_roundtrip_and_rail_binding():
    order = ev.create_multichain_order(
        "user-1", "premium_monthly", "evm_usdc", 12.0, quoted_amount=12.0
    )
    decoded = ev.verify_multichain_order_token(order["order_token"], "user-1")
    assert decoded["rail"] == "evm_usdc"
    assert decoded["p"] == "premium_monthly"
    assert float(decoded["usd"]) == 12.0


@pytest.mark.unit
def test_order_token_rejected_for_other_user():
    order = ev.create_multichain_order("user-1", "premium_monthly", "evm_usdc", 12.0)
    with pytest.raises(HTTPException) as exc:
        ev.verify_multichain_order_token(order["order_token"], "user-2")
    assert exc.value.status_code == 403


@pytest.mark.unit
def test_order_token_tamper_rejected():
    order = ev.create_multichain_order("user-1", "premium_monthly", "ton_native", 12.0)
    b64, sig = order["order_token"].split(".")
    tampered = b64[:-2] + ("A" if b64[-2] != "A" else "B") + "." + sig
    with pytest.raises(HTTPException) as exc:
        ev.verify_multichain_order_token(tampered, "user-1")
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_order_token_expired_rejected(monkeypatch):
    order = ev.create_multichain_order("user-1", "premium_monthly", "evm_usdc", 12.0)
    future = time.time() + 31 * 60
    monkeypatch.setattr(ev.time, "time", lambda: future)
    with pytest.raises(HTTPException) as exc:
        ev.verify_multichain_order_token(order["order_token"], "user-1")
    assert exc.value.status_code == 400


# --- One-Click Auth: wallet-composed ERC-4361 message ------------------------
# WalletConnect 的 One-Click Auth 由「錢包」自己組 ERC-4361 訊息，不是簽我們發的
# 字串——所以後端不能再用 nonce_token 重建訊息，必須驗證使用者送上來的 message
# 本身（nonce 是我們發的、domain 是我們的、地址對得上、簽章對得上）。


def _erc4361(
    *,
    domain: str,
    address: str,
    nonce: str,
    issued_at: str = "2026-09-01T12:00:00Z",
    uri: str | None = None,
    extra_lines: tuple[str, ...] = (),
) -> str:
    lines = [
        f"{domain} wants you to sign in with your Ethereum account:",
        address,
        "",
        "Sign in to CryptoMind",
        "",
        f"URI: {uri or ('https://' + domain)}",
        "Version: 1",
        "Chain ID: 1",
        f"Nonce: {nonce}",
        f"Issued At: {issued_at}",
    ]
    lines.extend(extra_lines)
    return "\n".join(lines)


@pytest.mark.unit
def test_verify_wallet_message_happy_path(wallet):
    addr, acct = wallet
    nonce = ev.generate_evm_nonce_payload()
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(addr),
        nonce=nonce,
    )
    assert (
        ev.verify_siwe_wallet_message(
            address=addr, message=msg, signature=_sign_message(acct, msg)
        )
        == addr
    )


@pytest.mark.unit
def test_verify_wallet_message_forged_nonce_rejected(wallet):
    addr, acct = wallet
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(addr),
        nonce="1788240000.deadbeefdeadbeef.00000000000000000000000000000000",
    )
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(
            address=addr, message=msg, signature=_sign_message(acct, msg)
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_expired_nonce_rejected(wallet, monkeypatch):
    addr, acct = wallet
    nonce = ev.generate_evm_nonce_payload()
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(addr),
        nonce=nonce,
    )
    sig = _sign_message(acct, msg)
    future = time.time() + ev.EVM_NONCE_PAYLOAD_TTL + 60
    monkeypatch.setattr(ev.time, "time", lambda: future)
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(address=addr, message=msg, signature=sig)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_wrong_domain_rejected(wallet):
    addr, acct = wallet
    nonce = ev.generate_evm_nonce_payload()
    msg = _erc4361(
        domain="evil.example.com",
        address=ev.to_checksum_address(addr),
        nonce=nonce,
        uri="https://evil.example.com",
    )
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(
            address=addr, message=msg, signature=_sign_message(acct, msg)
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_address_mismatch_rejected(wallet):
    _addr, acct = wallet
    other = Account.create()
    nonce = ev.generate_evm_nonce_payload()
    # 訊息裡宣稱的是別人的地址——簽章雖有效，也不能放行
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(other.address.lower()),
        nonce=nonce,
    )
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(
            address=other.address.lower(),
            message=msg,
            signature=_sign_message(acct, msg),
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_tampered_after_signing_rejected(wallet):
    addr, acct = wallet
    nonce = ev.generate_evm_nonce_payload()
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(addr),
        nonce=nonce,
    )
    sig = _sign_message(acct, msg)
    tampered = msg.replace("Chain ID: 1", "Chain ID: 8453")
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(address=addr, message=tampered, signature=sig)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_past_expiration_rejected(wallet):
    addr, acct = wallet
    nonce = ev.generate_evm_nonce_payload()
    msg = _erc4361(
        domain=ev._expected_domain(),
        address=ev.to_checksum_address(addr),
        nonce=nonce,
        extra_lines=("Expiration Time: 2020-01-01T00:00:00Z",),
    )
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(
            address=addr, message=msg, signature=_sign_message(acct, msg)
        )
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_verify_wallet_message_without_nonce_line_rejected(wallet):
    addr, acct = wallet
    msg = "just some text the wallet made up"
    with pytest.raises(HTTPException) as exc:
        ev.verify_siwe_wallet_message(
            address=addr, message=msg, signature=_sign_message(acct, msg)
        )
    assert exc.value.status_code == 401


# --- nonce 必須符合 ERC-4361 -------------------------------------------------
# ERC-4361 的 ABNF 是 `nonce = 8*( ALPHA / DIGIT )`——不含點。舊格式
# `<ts>.<rnd>.<sig>` 在 One-Click Auth 會交給錢包自己組訊息，嚴格照規格實作的
# 錢包會拒收。改成純十六進位，同時保留舊格式驗證（避免發出去的 nonce 當場失效）。


@pytest.mark.unit
def test_nonce_is_erc4361_alphanumeric():
    p = ev.generate_evm_nonce_payload()
    assert p.isalnum(), f"nonce 必須只有英數字：{p}"
    assert len(p) >= 8


@pytest.mark.unit
def test_legacy_dotted_nonce_still_accepted():
    """已經發到使用者手上的舊 nonce 不能因為改格式就當場失效。"""
    ts = int(time.time())
    body = f"{ts}.{os.urandom(8).hex()}"
    legacy = f"{body}.{ev._sign(body.encode())}"
    assert ev._check_payload(legacy) == ts


@pytest.mark.unit
def test_new_nonce_tampered_rejected():
    p = ev.generate_evm_nonce_payload()
    tampered = p[:-1] + ("0" if p[-1] != "0" else "1")
    with pytest.raises(HTTPException) as exc:
        ev._check_payload(tampered)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_new_nonce_expired_rejected(monkeypatch):
    p = ev.generate_evm_nonce_payload()
    future = time.time() + ev.EVM_NONCE_PAYLOAD_TTL + 10
    monkeypatch.setattr(ev.time, "time", lambda: future)
    with pytest.raises(HTTPException) as exc:
        ev._check_payload(p)
    assert exc.value.status_code == 401


@pytest.mark.unit
def test_issue_challenge_still_works_with_new_nonce(wallet):
    addr, acct = wallet
    challenge = ev.issue_siwe_challenge(addr)
    assert challenge["nonce_token"].isalnum()
    sig = _sign_message(acct, challenge["message"])
    assert (
        ev.recover_siwe_signer(
            address=addr, payload=challenge["nonce_token"], signature=sig
        )
        == addr
    )


@pytest.mark.unit
def test_issue_challenge_without_address_for_one_click_auth():
    """One-Click Auth 在還不知道地址時就要 nonce（地址是錢包批准後才有的）。"""
    challenge = ev.issue_siwe_challenge(None)
    assert challenge["nonce_token"].isalnum()
    assert challenge["address"] is None
    # 沒有地址就組不出訊息，也不該假裝組得出來
    assert challenge["message"] is None


@pytest.mark.unit
def test_issue_challenge_still_rejects_malformed_address():
    with pytest.raises(HTTPException) as exc:
        ev.issue_siwe_challenge("not-an-address")
    assert exc.value.status_code == 400


@pytest.mark.unit
def test_siwe_message_is_valid_erc4361():
    """Base App／Coinbase Wallet 的 provider 照 EIP-4361 嚴格解析；格式不對 personal_sign
    直接「Invalid message」（2026-09-13 mini app 實測）。"""
    payload = ev.generate_evm_nonce_payload()
    ts = ev._check_payload(payload)
    addr = "0x" + "ab" * 20
    msg = ev.build_siwe_message(addr, payload, ts, domain="cryptomind-ton.zeabur.app")
    lines = msg.split("\n")
    assert (
        lines[0]
        == "cryptomind-ton.zeabur.app wants you to sign in with your Ethereum account:"
    )
    assert lines[1] == ev.to_checksum_address(addr)
    assert lines[2] == "" and lines[3] == ev.SIWE_STATEMENT and lines[4] == ""
    assert lines[5] == "URI: https://cryptomind-ton.zeabur.app"
    assert lines[6] == "Version: 1"
    assert lines[7] == f"Chain ID: {ev.SIWE_CHAIN_ID}"
    assert lines[8] == f"Nonce: {payload}" and payload.isalnum() and len(payload) >= 8
    assert lines[9].startswith("Issued At: ") and lines[9].endswith("Z")
    assert "Domain:" not in msg and "Issued:" not in msg
    # 本機帶 port：URI 用 http
    local = ev.build_siwe_message(addr, payload, ts, domain="localhost:8080")
    assert "URI: http://localhost:8080" in local
    # 後端自己的 ERC-4361 解析器要吃得下（One-Click 路徑共用）
    parsed_domain = (
        ev._SIWE_FIRST_LINE.match(lines[0]) if hasattr(ev, "_SIWE_FIRST_LINE") else None
    )
    assert (
        parsed_domain is None
        or parsed_domain.group("domain") == "cryptomind-ton.zeabur.app"
    )
