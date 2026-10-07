"""Unit tests for user_wallets write-side repo + SIWE challenge (c041 Part A/B).

Reads (get_binding / list_for_user) are deferred pending the Mimosa gate fix
(see core/orm/wallets_repo.py module docstring) — only write semantics and
the challenge builder are covered here.
"""


import pytest
from sqlalchemy.exc import IntegrityError

import api.evm_verification as ev
from core.orm import wallets_repo
from core.orm.wallets_repo import user_wallet_repo


class _FakeResult:
    """Mimics SQLAlchemy Result.scalars().all() for list_for_user."""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Minimal async session: records adds; flush raises when configured to.

    ``get`` serves the PK-lookup read path (Session.get with (chain, address));
    ``execute`` serves list_for_user.
    """

    def __init__(
        self,
        fail_flush: bool = False,
        pk_rows: dict | None = None,
        list_rows: list | None = None,
    ):
        self.added = []
        self._fail_flush = fail_flush
        self.rolled_back = False
        self._pk_rows = pk_rows or {}
        self._list_rows = list_rows or []

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self._fail_flush:
            raise IntegrityError("INSERT", {}, Exception("uq_user_wallets_chain_address"))

    async def rollback(self):
        self.rolled_back = True

    async def get(self, entity, ident):
        return self._pk_rows.get(ident)

    async def execute(self, stmt):
        return _FakeResult(self._list_rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def _patch_session(monkeypatch, fake):
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_using_session(session=None):
        yield fake

    monkeypatch.setattr(wallets_repo, "using_session", _fake_using_session)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_binding_success(monkeypatch):
    fake = _FakeSession(fail_flush=False)
    _patch_session(monkeypatch, fake)
    result = await user_wallet_repo.register_binding(
        "u1", "evm", "0xabc0000000000000000000000000000000000000", is_primary=True
    )
    assert result["user_id"] == "u1"
    assert result["chain"] == "evm"
    assert result["is_primary"] is True
    assert len(fake.added) == 1
    assert fake.rolled_back is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_register_binding_conflict_fail_closed(monkeypatch):
    fake = _FakeSession(fail_flush=True)
    _patch_session(monkeypatch, fake)
    with pytest.raises(ValueError) as exc:
        await user_wallet_repo.register_binding(
            "u1", "evm", "0xabc0000000000000000000000000000000000000"
        )
    assert "already bound" in str(exc.value)
    assert fake.rolled_back is True


# --- PK-lookup read (Session.get) ---------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_binding_found(monkeypatch):
    row = wallets_repo.UserWallet(
        user_id="ton-account",
        chain="evm",
        address="0xabc0000000000000000000000000000000000000",
        is_primary=False,
    )
    fake = _FakeSession(
        pk_rows={("evm", "0xabc0000000000000000000000000000000000000"): row}
    )
    _patch_session(monkeypatch, fake)
    result = await user_wallet_repo.get_binding(
        "evm", "0xabc0000000000000000000000000000000000000"
    )
    assert result["user_id"] == "ton-account"
    assert result["chain"] == "evm"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_binding_miss_returns_none(monkeypatch):
    fake = _FakeSession()
    _patch_session(monkeypatch, fake)
    result = await user_wallet_repo.get_binding(
        "evm", "0xdead0000000000000000000000000000000000"
    )
    assert result is None


# --- list_for_user / bound_addresses (payment payer binding) -------------------


def _row(user_id, chain, address, is_primary=False):
    return wallets_repo.UserWallet(
        user_id=user_id, chain=chain, address=address, is_primary=is_primary
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_for_user_returns_rows(monkeypatch):
    fake = _FakeSession(
        list_rows=[
            _row("u1", "ton", "EQTon", is_primary=True),
            _row("u1", "evm", "0xabc0000000000000000000000000000000000000"),
        ]
    )
    _patch_session(monkeypatch, fake)
    rows = await user_wallet_repo.list_for_user("u1")
    assert len(rows) == 2
    assert {r["chain"] for r in rows} == {"ton", "evm"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bound_addresses_filters_chain_and_preserves_case(monkeypatch):
    fake = _FakeSession(
        list_rows=[
            _row("u1", "evm", "0xABC0000000000000000000000000000000000000"),
            _row("u1", "ton", "EQTon"),
        ]
    )
    _patch_session(monkeypatch, fake)
    evm = await user_wallet_repo.bound_addresses("u1", "evm")
    ton = await user_wallet_repo.bound_addresses("u1", "ton")
    # returned verbatim: TON friendly addresses are case-sensitive (CRC16)
    assert evm == ["0xABC0000000000000000000000000000000000000"]
    assert ton == ["EQTon"]


# --- SIWE challenge helper ----------------------------------------------------


@pytest.mark.unit
def test_issue_siwe_challenge_shape_and_determinism():
    addr = "0x" + "ab" * 20
    challenge = ev.issue_siwe_challenge(addr)
    assert challenge["address"] == addr
    # ERC-4361 的 nonce 只能是英數字——One-Click Auth 由錢包組訊息，帶點的
    # nonce 會被照規格實作的錢包拒收
    assert challenge["nonce_token"].isalnum()
    assert f"Nonce: {challenge['nonce_token']}" in challenge["message"]
    # EIP-4361：第一行是「{domain} wants you to sign in with your Ethereum account:」，
    # 沒有「Domain: 」標籤（0436ba2 改成標準格式後這條斷言沒跟上）
    first_line = challenge["message"].split("\n", 1)[0]
    assert first_line.endswith(" wants you to sign in with your Ethereum account:")
    # the exact message the client signs must verify round-trip server-side
    assert challenge["message"] == ev.build_siwe_message(
        addr, challenge["nonce_token"], ev._check_payload(challenge["nonce_token"])
    )
    assert challenge["expires_in"] == ev.EVM_NONCE_PAYLOAD_TTL


@pytest.mark.unit
def test_issue_siwe_challenge_rejects_bad_address():
    with pytest.raises(Exception):
        ev.issue_siwe_challenge("not-an-address")


# --- multichain gate flag -----------------------------------------------------


@pytest.mark.unit
def test_multichain_enabled_flag_exists():
    from core.config import MULTICHAIN_ENABLED

    assert isinstance(MULTICHAIN_ENABLED, bool)
