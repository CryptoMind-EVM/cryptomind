"""EVM 地址綁定測試 — 簽章驗證、nonce、綁定流程、scoring 接 Passport

涵蓋：
    - 單元：normalize_evm_address、generate/check payload、verify_evm_signature（用真實 eth_account 簽章）
    - scoring：collect_passport_stamps 用綁定的 EVM 地址（mock Passport API）
    - router：nonce/bind/unbind 流程

對應 docs/plans/2026-08-08-evm-address-binding-design.md。
"""

from unittest.mock import AsyncMock, patch

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

# ──────────────────────────────────────────────────────────────────────────────
# 單元：地址正規化
# ──────────────────────────────────────────────────────────────────────────────


def test_normalize_valid_evm_address():
    from core.identity.evm_bind import normalize_evm_address

    assert normalize_evm_address("0x" + "a" * 40) == "0x" + "a" * 40
    # 大寫轉小寫
    assert normalize_evm_address("0x" + "A" * 40) == "0x" + "a" * 40


def test_normalize_invalid_evm_address():
    from core.identity.evm_bind import normalize_evm_address

    assert normalize_evm_address("") is None
    assert normalize_evm_address("EQabc") is None  # TON 地址
    assert normalize_evm_address("0x123") is None  # 太短
    assert normalize_evm_address("0x" + "g" * 40) is None  # 非 hex
    assert normalize_evm_address("xyz" + "a" * 40) is None  # 沒 0x


# ──────────────────────────────────────────────────────────────────────────────
# 單元：nonce payload 產生與驗證
# ──────────────────────────────────────────────────────────────────────────────


def test_payload_roundtrip_valid():
    from core.identity.evm_bind import _check_payload, generate_evm_bind_payload

    payload = generate_evm_bind_payload()
    assert _check_payload(payload) is True


def test_payload_rejects_tampered():
    from core.identity.evm_bind import _check_payload, generate_evm_bind_payload

    payload = generate_evm_bind_payload()
    # 竄改 signature 段
    ts, rnd, _sig = payload.split(".")
    tampered = f"{ts}.{rnd}.deadbeef"
    assert _check_payload(tampered) is False


def test_payload_rejects_garbage():
    from core.identity.evm_bind import _check_payload

    assert _check_payload("garbage") is False
    assert _check_payload("") is False


# ──────────────────────────────────────────────────────────────────────────────
# 單元：verify_evm_signature（用真實 eth_account 簽章）
# ──────────────────────────────────────────────────────────────────────────────


def test_verify_signature_valid():
    """真實帳號簽的章 → verify 通過。"""
    from core.identity.evm_bind import (
        build_sign_message,
        generate_evm_bind_payload,
        verify_evm_signature,
    )

    acct = Account.create()
    payload = generate_evm_bind_payload()
    message = build_sign_message(payload)
    signed = Account.sign_message(encode_defunct(text=message), acct.key)

    assert verify_evm_signature(
        evm_address=acct.address,
        signature=signed.signature.hex(),
        payload=payload,
    ) is True


def test_verify_signature_wrong_address():
    """A 簽的章，宣稱是 B 的地址 → verify 失敗（防綁別人地址）。"""
    from core.identity.evm_bind import (
        build_sign_message,
        generate_evm_bind_payload,
        verify_evm_signature,
    )

    signer = Account.create()
    victim = Account.create()
    payload = generate_evm_bind_payload()
    message = build_sign_message(payload)
    signed = Account.sign_message(encode_defunct(text=message), signer.key)

    assert verify_evm_signature(
        evm_address=victim.address,  # 宣稱是 victim，但實際 signer 簽的
        signature=signed.signature.hex(),
        payload=payload,
    ) is False


def test_verify_signature_bad_payload():
    """過期/偽造的 payload → verify 失敗，即使簽章對。"""
    from core.identity.evm_bind import build_sign_message, verify_evm_signature

    acct = Account.create()
    message = build_sign_message("fake.payload.123")
    signed = Account.sign_message(encode_defunct(text=message), acct.key)
    assert verify_evm_signature(
        evm_address=acct.address,
        signature=signed.signature.hex(),
        payload="fake.payload.123",
    ) is False


def test_verify_signature_malformed():
    """格式錯誤的簽章 → 不報錯，回 False。"""
    from core.identity.evm_bind import generate_evm_bind_payload, verify_evm_signature

    payload = generate_evm_bind_payload()
    assert verify_evm_signature(
        evm_address="0x" + "a" * 40,
        signature="not-a-signature",
        payload=payload,
    ) is False


# ──────────────────────────────────────────────────────────────────────────────
# scoring：collect_passport_stamps 用綁定的 EVM 地址
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_passport_stamps_uses_bound_evm_address():
    """綁定 EVM 地址 + Passport 分 ≥20 → stamp + 漸進加分。"""
    from core.identity import scoring

    with patch("core.identity.scoring.HUMAN_PASSPORT_SCORER_ID", "12146"), \
         patch("core.identity.scoring.HUMAN_PASSPORT_API_KEY", "fake-key"), \
         patch("core.database.user.get_user_evm_address", return_value="0x" + "a" * 40), \
         patch("core.identity.human_passport.fetch_passport_score",
               new=AsyncMock(return_value=25.5)):
        result = await scoring.collect_passport_stamps("EQtonuser")

    assert result["stamps"] == {"gitcoin_passport": True}
    assert result["reason"] == "verified"
    assert result["passport_score"] == 25.5
    # 漸進加分：25.5/100 × 20 = 5.1
    assert result["graduated_bonus"] == 5.1


@pytest.mark.asyncio
async def test_collect_passport_stamps_graduated_bonus_scales_with_score():
    """漸進加分核心：分數越高 bonus 越高（修掉「20/0 二選一」缺陷）。"""
    from core.identity import scoring

    async def _run(score):
        with patch("core.identity.scoring.HUMAN_PASSPORT_SCORER_ID", "12146"), \
             patch("core.identity.scoring.HUMAN_PASSPORT_API_KEY", "fake-key"), \
             patch("core.database.user.get_user_evm_address", return_value="0x" + "a" * 40), \
             patch("core.identity.human_passport.fetch_passport_score",
                   new=AsyncMock(return_value=score)):
            return await scoring.collect_passport_stamps("EQtonuser")

    # 19 分（差一點過門檻）→ 不再歸零，有漸進加分 3.8
    low = await _run(19.0)
    assert low["graduated_bonus"] == 3.8
    assert low["stamps"] == {}  # 未過門檻 → 無 stamp，但仍加分

    # 80 分（努力養 stamps）→ 加分 16，遠高於 19 分
    high = await _run(80.0)
    assert high["graduated_bonus"] == 16.0
    assert high["stamps"] == {"gitcoin_passport": True}

    # 100 分（滿分）→ cap 20
    maxed = await _run(100.0)
    assert maxed["graduated_bonus"] == 20.0


@pytest.mark.asyncio
async def test_collect_passport_stamps_no_evm_bound():
    """未綁 EVM 地址 → 不計分。"""
    from core.identity import scoring

    with patch("core.identity.scoring.HUMAN_PASSPORT_SCORER_ID", "12146"), \
         patch("core.identity.scoring.HUMAN_PASSPORT_API_KEY", "fake-key"), \
         patch("core.database.user.get_user_evm_address", return_value=None):
        result = await scoring.collect_passport_stamps("EQtonuser")

    assert result["stamps"] == {}
    assert result["reason"] == "evm_address_not_bound"


@pytest.mark.asyncio
async def test_collect_passport_stamps_below_threshold():
    """Passport 分 < 20 → 無 stamp，但有漸進加分（不再歸零）。"""
    from core.identity import scoring

    with patch("core.identity.scoring.HUMAN_PASSPORT_SCORER_ID", "12146"), \
         patch("core.identity.scoring.HUMAN_PASSPORT_API_KEY", "fake-key"), \
         patch("core.database.user.get_user_evm_address", return_value="0x" + "b" * 40), \
         patch("core.identity.human_passport.fetch_passport_score",
               new=AsyncMock(return_value=5.0)):
        result = await scoring.collect_passport_stamps("EQtonuser")

    assert result["stamps"] == {}
    assert result["reason"] == "below_threshold"
    # 5 分 → 5/100 × 20 = 1.0（低分但有漸進加分）
    assert result["graduated_bonus"] == 1.0


@pytest.mark.asyncio
async def test_collect_passport_stamps_key_not_configured():
    """key 未設 → 直接回 not_configured，不打 API。"""
    from core.identity import scoring

    with patch("core.identity.scoring.HUMAN_PASSPORT_SCORER_ID", ""), \
         patch("core.identity.scoring.HUMAN_PASSPORT_API_KEY", ""):
        result = await scoring.collect_passport_stamps("EQtonuser")

    assert result["stamps"] == {}
    assert result["reason"] == "passport_not_configured"


# ──────────────────────────────────────────────────────────────────────────────
# Router：nonce / bind / unbind
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_evm_nonce_returns_payload_and_message(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_user_evm_address", return_value=None):
        response = await client.get(
            "/api/trust/evm/nonce", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    payload = response.json()
    assert "payload" in payload
    assert "message" in payload
    assert "Bind EVM address" in payload["message"]


@pytest.mark.asyncio
async def test_bind_evm_valid_signature_succeeds(client):
    """完整綁定流程：nonce → 簽章 → bind → 成功。"""
    from api.deps import create_access_token
    from core.identity.evm_bind import build_sign_message

    acct = Account.create()
    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    # 先取 nonce
    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_user_evm_address", return_value=None):
        nonce_resp = await client.get(
            "/api/trust/evm/nonce", headers={"Authorization": f"Bearer {token}"}
        )
    payload = nonce_resp.json()["payload"]
    message = build_sign_message(payload)
    signed = Account.sign_message(encode_defunct(text=message), acct.key)

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.is_evm_address_bound", return_value=False), \
         patch("api.routers.trust.set_user_evm_address", return_value=(True, "ok")):
        response = await client.post(
            "/api/trust/evm/bind",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "evm_address": acct.address,
                "signature": signed.signature.hex(),
                "payload": payload,
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["evm_address"] == acct.address.lower()


@pytest.mark.asyncio
async def test_bind_evm_wrong_signature_rejected(client):
    """錯誤簽章 → 403。"""
    from api.deps import create_access_token
    from core.identity.evm_bind import build_sign_message

    signer = Account.create()
    victim = Account.create()
    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_user_evm_address", return_value=None):
        nonce_resp = await client.get(
            "/api/trust/evm/nonce", headers={"Authorization": f"Bearer {token}"}
        )
    payload = nonce_resp.json()["payload"]
    message = build_sign_message(payload)
    signed = Account.sign_message(encode_defunct(text=message), signer.key)

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)):
        response = await client.post(
            "/api/trust/evm/bind",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "evm_address": victim.address,  # 宣稱 victim，但 signer 簽的
                "signature": signed.signature.hex(),
                "payload": payload,
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bind_evm_duplicate_address_conflict(client):
    """地址已被別人綁 → 409。"""
    from api.deps import create_access_token
    from core.identity.evm_bind import build_sign_message

    acct = Account.create()
    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.get_user_evm_address", return_value=None):
        nonce_resp = await client.get(
            "/api/trust/evm/nonce", headers={"Authorization": f"Bearer {token}"}
        )
    payload = nonce_resp.json()["payload"]
    message = build_sign_message(payload)
    signed = Account.sign_message(encode_defunct(text=message), acct.key)

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.is_evm_address_bound", return_value=True):  # 已被綁
        response = await client.post(
            "/api/trust/evm/bind",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "evm_address": acct.address,
                "signature": signed.signature.hex(),
                "payload": payload,
            },
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_unbind_evm_succeeds(client):
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)), \
         patch("api.routers.trust.clear_user_evm_address", return_value=(True, "ok")):
        response = await client.delete(
            "/api/trust/evm/unbind", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_bind_invalid_address_format_rejected(client):
    """非 EVM 格式 → 400（不浪費簽章驗證）。"""
    from api.deps import create_access_token

    token = create_access_token(data={"sub": "EQuser"})
    mocked_user = {
        "user_id": "EQuser", "username": "User", "role": "user",
        "auth_method": "ton_wallet", "is_active": True,
    }

    with patch("api.deps.user_repo.get_by_id", new=AsyncMock(return_value=mocked_user)):
        response = await client.post(
            "/api/trust/evm/bind",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "evm_address": "EQtonaddress",  # TON 地址，非 EVM
                "signature": "0x" + "0" * 130,
                "payload": "fake.payload.sig",
            },
        )

    assert response.status_code == 422  # Pydantic Field(42 chars) 先擋住非 EVM 格式
