"""EVM 登入自動綁定 Human Passport 地址（2026-09-08）。

背景：#trust 頁要求 EVM 登入使用者對「登入用的同一個錢包」再簽一次名——
但 SIWE 登入簽章本身就是所有權證明。業界標準（Galxe 模型、ERC-4361
生態）：**登入錢包在登入當下驗證，之後附加的錢包才需要獨立簽章**。

修法：
- ``evm_login`` 驗章成功後，若 ``users.evm_address`` 為空則自動寫入
  （只在空時寫：已手動綁了別的地址不覆蓋；任何失敗只記 log，
  **絕不擋登入**）
- ``/api/trust/score`` 偵測 breakdown 與 ``users.evm_address`` 漂移時
  惰性重算（同檔既有 first_access / legacy_unknown_repair 模式）——
  auto-bind 不在登入路徑付出重算延遲，分數在下次造訪 trust 頁時刷新

本檔守住：
- 登入自動綁定（新用戶與跨鏈歸戶兩條路徑都要）
- 已有綁定不覆蓋（手動綁了其他地址的人不受影響）
- attestation 的任何失敗都不能讓登入失敗（同步例外／unique 衝突）
- 漂移重算的兩個方向（綁上／解綁）與「一致就不重算」
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

ADDR = "0x" + "ab" * 20
ADDR_LOWER = ADDR.lower()


def _login_user(addr=ADDR_LOWER):
    return {
        "user_id": f"evm_{addr}",
        "username": "EVM_" + addr[2:8],
        "auth_method": "evm_wallet",
        "role": "user",
        "membership_tier": "free",
        "is_new": True,
    }


@contextmanager
def _login_env(addr=ADDR_LOWER, binding=None, login_user=None):
    """evm-login 路由的最小 patch 集：跳過簽章密碼學與 DB，聚焦 autobind。"""
    login_user = login_user or _login_user(addr)
    patchers = [
        patch("api.routers.user.MULTICHAIN_ENABLED", True),
        patch(
            "api.routers.user.recover_siwe_signer_async",
            new=AsyncMock(return_value=addr),
        ),
        patch("api.routers.user._enforce_auth_lockout"),
        patch("api.routers.user._clear_auth_failures"),
        patch("api.routers.user.audit_log"),
        patch(
            "api.routers.user.user_wallet_repo.get_binding",
            new=AsyncMock(return_value=binding),
        ),
        patch("api.routers.user.create_or_get_user", return_value=login_user),
        patch("api.routers.user.user_wallet_repo.register_binding", new=AsyncMock()),
    ]
    with ExitStack() as stack:
        yield [stack.enter_context(p) for p in patchers]


async def _do_login(client):
    return await client.post(
        "/api/user/evm-login",
        json={"address": ADDR, "signature": "0x" + "0" * 130, "nonce_token": "tok"},
    )


class TestLoginAutobinds:
    async def test_new_user_login_writes_trust_address(self, client):
        with (
            _login_env(),
            patch(
                "api.routers.user.get_user_evm_address", return_value=None
            ) as get_mock,
            patch(
                "api.routers.user.set_user_evm_address", return_value=(True, "ok")
            ) as set_mock,
        ):
            resp = await _do_login(client)

        assert resp.status_code == 200
        get_mock.assert_called_once_with(f"evm_{ADDR_LOWER}")
        set_mock.assert_called_once_with(f"evm_{ADDR_LOWER}", ADDR_LOWER)

    async def test_crosschain_login_also_autobinds(self, client):
        """跨鏈歸戶（地址已屬於既有帳號）——登入該帳號時同樣補綁定。"""
        existing = _login_user()
        existing["user_id"] = "EQexisting_ton_user"  # get_binding 命中時身分不是 evm_
        with _login_env(
            binding={"user_id": "EQexisting_ton_user"}, login_user=existing
        ):
            with (
                patch("api.routers.user.get_user_evm_address", return_value=None),
                patch(
                    "api.routers.user.set_user_evm_address", return_value=(True, "ok")
                ) as set_mock,
            ):
                resp = await _do_login(client)

        assert resp.status_code == 200
        set_mock.assert_called_once_with("EQexisting_ton_user", ADDR_LOWER)

    async def test_autobind_respects_flag_rollback(self, client):
        """TRUST_EVM_BINDING_ENABLED 關閉時不寫（回滾路徑與手動綁定端點一致）。"""
        with (
            _login_env(),
            patch("api.routers.user.TRUST_EVM_BINDING_ENABLED", False),
            patch("api.routers.user.set_user_evm_address") as set_mock,
        ):
            resp = await _do_login(client)

        assert resp.status_code == 200
        set_mock.assert_not_called()


class TestAutobindNeverBlocksLogin:
    async def test_existing_binding_not_overwritten(self, client):
        """已手動綁了其他地址 → 不覆蓋（空值檢查擋在 set 之前）。"""
        with (
            _login_env(),
            patch(
                "api.routers.user.get_user_evm_address", return_value="0x" + "cc" * 20
            ),
            patch("api.routers.user.set_user_evm_address") as set_mock,
        ):
            resp = await _do_login(client)

        assert resp.status_code == 200
        set_mock.assert_not_called()

    async def test_duplicate_conflict_does_not_fail_login(self, client):
        """歷史資料兩帳號宣稱同地址（unique 衝突）→ 記 log，登入照常。"""
        with (
            _login_env(),
            patch("api.routers.user.get_user_evm_address", return_value=None),
            patch(
                "api.routers.user.set_user_evm_address",
                return_value=(False, "duplicate"),
            ),
        ):
            resp = await _do_login(client)

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_sync_exception_does_not_fail_login(self, client):
        """綁定庫整個炸掉（欄位未 migrate 等）→ 登入照常。"""
        with (
            _login_env(),
            patch(
                "api.routers.user.get_user_evm_address",
                side_effect=RuntimeError("db down"),
            ),
        ):
            resp = await _do_login(client)

        assert resp.status_code == 200
        assert resp.json()["success"] is True


def _latest(passport_addr=None, tier="known_wallet", score=40):
    passport = {"reason": "verified" if passport_addr else "evm_address_not_bound"}
    if passport_addr:
        passport["evm_address"] = passport_addr
    return {
        "trust_score": score,
        "tier": tier,
        "breakdown": {"passport": passport},
        "computed_at": "2026-09-08T00:00:00+00:00",
        "recompute_reason": "first_access",
    }


@contextmanager
def _score_env(latest, bound_now, fresh):
    """trust /score 的最小 patch 集。get_latest_trust_score 依序回 stale → fresh。"""
    patchers = [
        patch("api.routers.trust.TRUST_SCORE_ENABLED", True),
        patch("api.routers.trust.TRUST_EVM_BINDING_ENABLED", True),
        patch(
            "api.deps.user_repo.get_by_id",
            new=AsyncMock(
                return_value={
                    "user_id": "EQuser",
                    "username": "User",
                    "role": "user",
                    "auth_method": "ton_wallet",
                    "is_active": True,
                }
            ),
        ),
        patch("api.routers.trust.get_latest_trust_score", side_effect=[latest, fresh]),
        patch("api.routers.trust.get_user_evm_address", return_value=bound_now),
    ]
    with ExitStack() as stack:
        yield [stack.enter_context(p) for p in patchers]


class TestScoreDriftRecompute:
    async def test_bind_drift_triggers_recompute(self, client):
        """breakdown 說未綁、DB 已綁（登入 auto-bind）→ 重算一次。"""
        fresh = _latest(passport_addr=ADDR_LOWER, tier="soft_verified", score=65)
        with _score_env(_latest(passport_addr=None), ADDR_LOWER, fresh):
            with patch(
                "api.routers.trust.recompute_user_trust",
                new=AsyncMock(
                    return_value={"trust_score": 65, "tier": "soft_verified"}
                ),
            ) as rc:
                resp = await client.get("/api/trust/score")

        assert resp.status_code == 200
        rc.assert_awaited_once()
        assert rc.await_args.kwargs.get("reason") == "evm_binding_changed"

    async def test_unbind_drift_triggers_recompute(self, client):
        """breakdown 記著地址、DB 已清（解綁）→ 重算。"""
        fresh = _latest(passport_addr=None, tier="anonymous", score=10)
        with _score_env(_latest(passport_addr=ADDR_LOWER), None, fresh):
            with patch(
                "api.routers.trust.recompute_user_trust",
                new=AsyncMock(return_value={"trust_score": 10, "tier": "anonymous"}),
            ) as rc:
                resp = await client.get("/api/trust/score")

        assert resp.status_code == 200
        rc.assert_awaited_once()

    async def test_consistent_state_does_not_recompute(self, client):
        """兩邊一致（同地址／同為空）→ 不重算。"""
        for latest, bound in (
            (_latest(passport_addr=ADDR_LOWER), ADDR_LOWER),
            (_latest(passport_addr=None), None),
        ):
            fresh = _latest(passport_addr=bound)
            with _score_env(latest, bound, fresh):
                with patch(
                    "api.routers.trust.recompute_user_trust",
                    new=AsyncMock(return_value={"trust_score": 1, "tier": "anonymous"}),
                ) as rc:
                    resp = await client.get("/api/trust/score")

            assert resp.status_code == 200
            rc.assert_not_awaited()
