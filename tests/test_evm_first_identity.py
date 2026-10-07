"""身份／錢包改成 EVM 優先（2026-09-25，TON 盤查 A5–A8）。

登入 2026-08-31 起只剩 EVM／Telegram／Google，但身份相關的判斷還停在 TON 時代：

- A5 Agent 的 principal 與招呼只認 TON 身份——EVM 使用者一律被當成「沒有錢包」。
- A6 信任分數只重算 TON 身份、詐騙扣分拿 ``evm_0x…`` 這個 user_id 去比對（永遠對不到）、
  wallet_verified 對每個人都是 True（Telegram-only 帳號也拿「錢包所有權」30 分）。
- A7 has_wallet = auth_method is not None——Telegram／Google 帳號全被當成有錢包；
  後台「錢包用戶」只數 auth_method='ton_wallet'。
- A8 建帳預設 auth_method='ton_wallet'、username 'TON_…'；暱稱保留字只擋 TON_。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

EVM = "0x3304e22ddaa22bcdc5fca2269b418046ae7b566a"
EVM_ID = f"evm_{EVM}"
# 真的 TON 地址（is_ton_address 會解碼驗 CRC）
TON = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"


def _fake_conn(cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


# ──────────────────────────────────────────────────────────────────────────────
# 共用：身份 → 錢包地址
# ──────────────────────────────────────────────────────────────────────────────


class TestWalletIdentity:
    def test_evm_identity_maps_to_address(self):
        from core.onchain.addresses import evm_identity_address, wallet_identity_address

        assert evm_identity_address(EVM_ID) == EVM
        assert evm_identity_address("evm_" + EVM.upper().replace("0X", "0x")) == EVM
        assert wallet_identity_address(EVM_ID) == EVM

    def test_legacy_ton_identity_is_a_wallet(self):
        from core.onchain.addresses import evm_identity_address, wallet_identity_address

        assert wallet_identity_address(TON) == TON
        assert evm_identity_address(TON) is None

    @pytest.mark.parametrize(
        "uid", ["tg_42", "g_1089012345", "test-user-001", "evm_nope", "", None]
    )
    def test_non_wallet_identities(self, uid):
        from core.onchain.addresses import wallet_identity_address

        assert wallet_identity_address(uid) is None


class TestVerifiedEvmAddress:
    def test_prefers_user_wallets_row(self):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchone.return_value = ("0xAbC0000000000000000000000000000000000001",)
        with (
            patch.object(mod, "get_connection", return_value=_fake_conn(cur)),
            patch.object(mod, "get_user_evm_address", return_value="0x" + "9" * 40),
        ):
            assert (
                mod.get_verified_evm_address("tg_42")
                == "0xabc0000000000000000000000000000000000001"
            )
        sql = cur.execute.call_args.args[0]
        assert "user_wallets" in sql and "'evm'" in sql

    def test_falls_back_to_identity_then_users_column(self):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchone.return_value = None
        with (
            patch.object(mod, "get_connection", return_value=_fake_conn(cur)),
            patch.object(mod, "get_user_evm_address", return_value="0x" + "9" * 40),
        ):
            assert mod.get_verified_evm_address(EVM_ID) == EVM
            assert mod.get_verified_evm_address("tg_42") == "0x" + "9" * 40

    def test_nothing_bound(self):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchone.return_value = None
        with (
            patch.object(mod, "get_connection", return_value=_fake_conn(cur)),
            patch.object(mod, "get_user_evm_address", return_value=None),
        ):
            assert mod.get_verified_evm_address("tg_42") is None


# ──────────────────────────────────────────────────────────────────────────────
# A5：principal／招呼認得 EVM 使用者
# ──────────────────────────────────────────────────────────────────────────────


class TestAgentPrincipal:
    def test_system_prompt_names_evm_wallet(self):
        from core.agents.agents.cryptomind_agent import CryptoMindAgent

        agent = CryptoMindAgent.__new__(CryptoMindAgent)
        agent.display_name = "Alice"
        agent.wallet_address = None
        agent.evm_address = EVM
        agent.user_tier = "free"
        prompt = agent._get_system_prompt("en")
        assert "0x33...566a" in prompt
        assert "EVM signature" in prompt
        assert EVM not in prompt  # 完整地址不進 prompt

    def test_legacy_ton_identity_keeps_ton_line(self):
        from core.agents.agents.cryptomind_agent import CryptoMindAgent

        agent = CryptoMindAgent.__new__(CryptoMindAgent)
        agent.display_name = None
        agent.wallet_address = TON
        agent.evm_address = EVM
        agent.user_tier = "free"
        prompt = agent._get_system_prompt("en")
        assert "ton_proof" in prompt
        assert "0x33...566a" not in prompt


class TestResolvePrincipalWallets:
    async def test_ton_identity(self):
        from api.routers.analysis import _resolve_principal_wallets

        lookup = MagicMock(return_value=EVM)
        with patch("core.database.user.get_verified_evm_address", new=lookup):
            assert await _resolve_principal_wallets(TON) == (TON, None)
        lookup.assert_not_called()

    async def test_evm_user(self):
        from api.routers.analysis import _resolve_principal_wallets

        with patch("core.database.user.get_verified_evm_address", return_value=EVM):
            assert await _resolve_principal_wallets(EVM_ID) == (None, EVM)

    async def test_telegram_user_without_wallet(self):
        from api.routers.analysis import _resolve_principal_wallets

        with patch("core.database.user.get_verified_evm_address", return_value=None):
            assert await _resolve_principal_wallets("tg_42") == (None, None)

    async def test_lookup_failure_degrades_to_no_wallet(self):
        from api.routers.analysis import _resolve_principal_wallets

        with patch(
            "core.database.user.get_verified_evm_address",
            side_effect=RuntimeError("db down"),
        ):
            assert await _resolve_principal_wallets("tg_42") == (None, None)


@pytest.mark.asyncio
async def test_greeting_passes_evm_wallet(client, auth_headers, monkeypatch):
    from api.routers import analysis as analysis_router

    analysis_router._GREETING_CACHE.clear()
    analysis_router._GREETING_USER_RECENT.clear()
    captured = {}

    async def fake_generate(
        *, display_name, wallet_address, language, tier, current_user=None
    ):
        captured["wallet_address"] = wallet_address
        return "hi"

    monkeypatch.setattr(analysis_router, "_generate_greeting", fake_generate)
    monkeypatch.setattr("core.database.user.get_verified_evm_address", lambda uid: EVM)
    monkeypatch.setattr(analysis_router.limiter, "enabled", False)
    try:
        resp = await client.post(
            "/api/chat/greeting",
            json={"session_id": "sess-evm", "language": "en"},
            headers=auth_headers,
        )
    finally:
        analysis_router._GREETING_CACHE.clear()
        analysis_router._GREETING_USER_RECENT.clear()
    assert resp.status_code == 200, resp.text
    assert captured["wallet_address"] == EVM


async def test_greeting_hint_is_chain_neutral(monkeypatch):
    """招呼的錢包提示原本寫死「TON 錢包」——EVM 使用者會被叫成 TON 錢包持有者。"""
    from api.routers import analysis as analysis_router

    seen = {}

    class _LLM:
        async def ainvoke(self, messages):
            seen["user_msg"] = messages[1].content
            return MagicMock(content="hello")

    monkeypatch.setattr(
        analysis_router,
        "resolve_user_llm_credentials",
        AsyncMock(return_value={"provider": "openai", "api_key": "k", "model": "m"}),
    )
    monkeypatch.setattr(analysis_router, "create_user_llm_client", lambda **_: _LLM())
    await analysis_router._generate_greeting(
        display_name="Alice", wallet_address=EVM, language="en", tier="free"
    )
    assert "0x33...566a" in seen["user_msg"]
    assert "TON" not in seen["user_msg"]


def test_job_envelope_carries_evm_address():
    from api.routers.analysis import _build_job_envelope

    env = _build_job_envelope(
        run_id="r1",
        session_id="s1",
        user_id=EVM_ID,
        credentials={},
        graph_input=None,
        config={},
        evm_address=EVM,
    )
    assert env["evm_address"] == EVM


def test_worker_passes_evm_address_to_bootstrap():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1] / "scripts/analysis_worker.py"
    ).read_text(encoding="utf-8")
    assert 'evm_address=job.get("evm_address")' in src


def test_bootstrap_reapplies_evm_address_on_cache_hit():
    """快取命中時身份 attr 要重套（跟 wallet_address 同一套），否則換人還留著舊地址。"""
    import importlib

    mod = importlib.import_module("core.agents.bootstrap")
    agent = MagicMock(
        spec=[
            "llm",
            "user_tier",
            "user_id",
            "display_name",
            "wallet_address",
            "evm_address",
        ]
    )
    registry = MagicMock()
    registry._agents = {"cryptomind": agent}
    manager = MagicMock()
    manager.session_id = "s1"
    manager.agent_registry = registry
    key = mod._manager_cache_key("u1", "s1", "fp", "en")
    import time

    with mod._manager_cache_lock:
        mod._manager_cache[key] = (manager, time.time())
    try:
        mod.bootstrap(
            MagicMock(),
            language="en",
            user_id="u1",
            session_id="s1",
            key_fingerprint="fp",
            evm_address=EVM,
        )
    finally:
        with mod._manager_cache_lock:
            mod._manager_cache.pop(key, None)
    assert agent.evm_address == EVM


# ──────────────────────────────────────────────────────────────────────────────
# A6：信任分數
# ──────────────────────────────────────────────────────────────────────────────


def _scoring_patches(scoring, *, evm, scam=None, evm_onchain=None, linked=None):
    scam = scam or MagicMock(return_value={"penalty": 0, "reason": "clean"})
    evm_onchain = evm_onchain or AsyncMock(
        return_value={"first_active": None, "tx_count": None, "is_active": False}
    )
    return [
        patch.object(
            scoring,
            "collect_onchain_signals",
            new=AsyncMock(
                return_value={
                    "first_active": None,
                    "tx_count": None,
                    "is_active": False,
                }
            ),
        ),
        patch.object(
            scoring,
            "collect_passport_stamps",
            new=AsyncMock(
                return_value={"stamps": {}, "reason": "passport_not_configured"}
            ),
        ),
        patch.object(scoring, "collect_evm_onchain_signals", new=evm_onchain),
        patch.object(scoring, "collect_scam_penalty", new=scam),
        patch.object(
            scoring,
            "collect_activity_signals",
            return_value={"score": 0, "inactive_days": None, "reason": "never_active"},
        ),
        patch.object(scoring, "_persist_trust_score"),
        patch("core.database.user.get_verified_evm_address", return_value=evm),
    ] + (
        [patch("core.database.user.get_linked_wallet_addresses", return_value=linked)]
        if linked is not None
        else []
    )


async def _recompute(user_id, **kw):
    from contextlib import ExitStack

    from core.identity import scoring

    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _scoring_patches(scoring, **kw)]
        result = await scoring.recompute_user_trust(user_id, reason="test")
    return result, mocks


def _ownership(result):
    signals = result["breakdown"]["base_assessment"]["signals"]
    return next(s for s in signals if s["name"] == "wallet_ownership")


class TestTrustScoring:
    async def test_scam_penalty_looks_up_the_real_evm_address(self):
        scam = MagicMock(return_value={"penalty": 100, "reason": "scam_report_hit"})
        result, _ = await _recompute(EVM_ID, evm=EVM, scam=scam)
        looked_up = [c.args[0] for c in scam.call_args_list]
        assert EVM in looked_up
        assert EVM_ID not in looked_up
        assert result["breakdown"]["penalty_applied"] == 100

    async def test_telegram_only_user_is_not_wallet_verified(self):
        result, _ = await _recompute("tg_42", evm=None)
        own = _ownership(result)
        assert own["score"] == 0
        assert result["trust_score"] == 0

    async def test_bound_evm_wallet_counts_as_verified(self):
        result, _ = await _recompute("tg_42", evm=EVM)
        assert _ownership(result)["score"] > 0

    async def test_legacy_ton_identity_still_verified(self):
        result, _ = await _recompute(TON, evm=None)
        assert _ownership(result)["score"] > 0

    async def test_ownership_detail_is_chain_neutral(self):
        result, _ = await _recompute(EVM_ID, evm=EVM)
        assert "TON" not in _ownership(result)["detail"]

    async def test_evm_signal_queries_base(self):
        from core.identity import scoring

        evm_onchain = AsyncMock(
            return_value={
                "first_active": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "tx_count": 1,
                "is_active": True,
            }
        )
        result, _ = await _recompute(EVM_ID, evm=EVM, evm_onchain=evm_onchain)
        chains = {
            c.kwargs.get("chain_id", c.args[1] if len(c.args) > 1 else None)
            for c in evm_onchain.call_args_list
        }
        assert scoring.platform_evm_chain_id() in chains
        assert scoring.platform_evm_chain_id() in (8453, 84532)
        assert all(c.args[0] == EVM for c in evm_onchain.call_args_list)
        assert result["breakdown"]["merged_first_active"] is not None

    async def test_no_ton_lookup_for_non_ton_identity(self):
        _, mocks = await _recompute(EVM_ID, evm=EVM)
        ton_collector = mocks[0]
        ton_collector.assert_not_awaited()

    def test_evm_first_tx_cache_is_per_chain(self):
        """同一個地址在 Base 與以太坊主網的首筆交易不同——快取 key 要分鏈。"""
        from core.identity import onchain_signals

        assert "{chain_id}" in onchain_signals._EVM_TX_CACHE_KEY


OLD_EVM = "0x" + "a" * 40  # 換綁前的地址（還留在 users.evm_address）


def _scam_hit_on(*hit_addresses):
    hits = {a.lower() for a in hit_addresses}
    return MagicMock(
        side_effect=lambda a: (
            {"penalty": 100, "reason": "scam_report_hit"}
            if a.lower() in hits
            else {"penalty": 0, "reason": "clean"}
        )
    )


class TestScamPenaltyCoversEveryLinkedAddress:
    """換綁不能洗掉詐騙扣分（#876 security review MEDIUM-2）。

    find_user_ids_by_evm_address 從舊的 users.evm_address = A 找到主人，但
    get_verified_evm_address 回的是新綁的 B——扣分只查 B 的話，檢舉 A 等於沒扣。
    principal／鏈上訊號仍用主要地址；只有扣分要查使用者連結過的每一個地址。
    """

    async def test_report_on_old_address_still_penalizes_after_rebind(self):
        scam = _scam_hit_on(OLD_EVM)
        result, _ = await _recompute("tg_7", evm=EVM, scam=scam, linked=[EVM, OLD_EVM])
        assert result["breakdown"]["penalty_applied"] == 100
        assert OLD_EVM in [c.args[0] for c in scam.call_args_list]

    async def test_report_on_new_address_penalizes(self):
        scam = _scam_hit_on(EVM)
        result, _ = await _recompute("tg_7", evm=EVM, scam=scam, linked=[EVM, OLD_EVM])
        assert result["breakdown"]["penalty_applied"] == 100

    async def test_same_address_is_checked_once(self):
        scam = _scam_hit_on(EVM)
        result, _ = await _recompute(
            EVM_ID, evm=EVM, scam=scam, linked=[EVM, EVM.upper().replace("0X", "0x")]
        )
        assert [c.args[0].lower() for c in scam.call_args_list] == [EVM]
        assert result["breakdown"]["penalty_applied"] == 100

    async def test_ton_binding_and_identity_are_checked(self):
        scam = _scam_hit_on(TON)
        result, _ = await _recompute("tg_7", evm=None, scam=scam, linked=[TON])
        assert result["breakdown"]["penalty_applied"] == 100

    async def test_lookup_failure_falls_back_to_primary_addresses(self):
        from contextlib import ExitStack

        from core.identity import scoring

        scam = _scam_hit_on(EVM)
        with ExitStack() as stack:
            for p in _scoring_patches(scoring, evm=EVM, scam=scam):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "core.database.user.get_linked_wallet_addresses",
                    side_effect=RuntimeError("db down"),
                )
            )
            result = await scoring.recompute_user_trust(EVM_ID, reason="test")
        assert result["breakdown"]["penalty_applied"] == 100

    def test_linked_addresses_cover_bindings_identity_and_column(self):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchall.return_value = [
            (OLD_EVM.upper().replace("0X", "0x"),),
            (TON,),
            (EVM,),
        ]
        with (
            patch.object(mod, "get_connection", return_value=_fake_conn(cur)),
            patch.object(mod, "get_user_evm_address", return_value=OLD_EVM),
        ):
            addrs = mod.get_linked_wallet_addresses(EVM_ID)
        # evm_ 身份、user_wallets（evm＋ton）、users.evm_address；去重、不分大小寫
        assert addrs == [EVM, OLD_EVM, TON]
        sql, params = cur.execute.call_args.args
        assert "user_wallets" in sql and "LIMIT" in sql.upper()
        assert "chain" not in sql.split("WHERE", 1)[1]  # evm 與 ton 列都要
        assert params == (EVM_ID,)


class TestTrustCron:
    def test_selects_evm_users_and_every_ton_format(self):
        from scripts import cron_recompute_trust as mod

        rows = [
            (EVM_ID, True),
            ("tg_7", True),  # Telegram 帳號但綁了 EVM 錢包
            (TON, False),
            ("tg_42", False),  # 沒錢包
            ("A" * 48, False),  # 長度對、不是 TON 地址
        ]
        cur = MagicMock()
        cur.fetchall.return_value = rows
        with patch.object(mod, "get_connection", return_value=_fake_conn(cur)):
            users = mod._fetch_wallet_users()
        assert [u for u, _ in users] == [EVM_ID, "tg_7", TON]
        sql = cur.execute.call_args.args[0]
        assert "user_wallets" in sql and "evm_" in sql


class TestScamReportRecompute:
    async def test_evm_owner_is_recomputed(self):
        from api.routers.scam_tracker import reports

        recompute = AsyncMock(return_value={"trust_score": 0})
        owners = MagicMock(return_value=[EVM_ID, "tg_7"])
        with (
            patch.object(reports, "TRUST_EVENT_RECOMPUTE_ENABLED", True),
            patch("core.database.user.find_user_ids_by_evm_address", new=owners),
            patch("core.identity.scoring.recompute_user_trust", new=recompute),
        ):
            await reports._recompute_trust_on_scam_hit(EVM.upper().replace("0X", "0x"))
        owners.assert_called_once_with(EVM)
        called = [c.args[0] for c in recompute.await_args_list]
        assert called == [EVM_ID, "tg_7"]

    def test_find_owners_matches_bindings_identity_and_column(self):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchall.return_value = [(EVM_ID,), ("tg_7",)]
        with patch.object(mod, "get_connection", return_value=_fake_conn(cur)):
            assert mod.find_user_ids_by_evm_address(
                EVM.upper().replace("0X", "0x")
            ) == [EVM_ID, "tg_7"]
        sql, params = cur.execute.call_args.args
        assert "user_wallets" in sql and "evm_address" in sql
        assert EVM in params and EVM_ID in params


# ──────────────────────────────────────────────────────────────────────────────
# A7：has_wallet／後台錢包用戶
# ──────────────────────────────────────────────────────────────────────────────


def _orm_user(user_id, auth_method):
    from core.orm.models import User

    return User(
        user_id=user_id,
        username="u",
        auth_method=auth_method,
        role="user",
        is_active=True,
        membership_tier="free",
    )


class TestHasWallet:
    @pytest.mark.parametrize(
        "user_id, auth_method",
        [("tg_42", "telegram"), ("g_1089012345", "google"), ("tg_42", "ton_wallet")],
    )
    def test_telegram_or_google_only_has_no_wallet(self, user_id, auth_method):
        from core.orm.repositories import _user_to_dict

        assert _user_to_dict(_orm_user(user_id, auth_method))["has_wallet"] is False

    def test_bound_evm_wallet_counts(self):
        from core.orm.repositories import _user_to_dict

        user = _orm_user("tg_42", "telegram")
        assert _user_to_dict(user, has_bound_wallet=True)["has_wallet"] is True

    @pytest.mark.parametrize("user_id", [EVM_ID, TON])
    def test_wallet_identities(self, user_id):
        from core.orm.repositories import _user_to_dict

        assert _user_to_dict(_orm_user(user_id, None))["has_wallet"] is True

    async def test_get_by_id_reads_bound_flag(self):
        from contextlib import asynccontextmanager
        from types import SimpleNamespace

        from core.orm.repositories import user_repo

        user = _orm_user("tg_42", "telegram")
        session = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(one_or_none=lambda: (user, True))
            )
        )

        @asynccontextmanager
        async def _fake(existing=None):
            yield session

        with patch("core.orm.repositories.using_session", _fake):
            result = await user_repo.get_by_id("tg_42")
        assert result["has_wallet"] is True
        stmt = str(session.execute.await_args.args[0])
        assert "user_wallets" in stmt

    @pytest.mark.parametrize(
        "user_id, row, expected",
        [
            ("tg_42", ("telegram", False, None), (False, None)),
            (
                "tg_42",
                ("telegram", True, "0xabc0000000000000000000000000000000000001"),
                (True, "0xabc0000000000000000000000000000000000001"),
            ),
            (EVM_ID, ("evm_wallet", True, EVM), (True, EVM)),
            (TON, ("ton_wallet", False, None), (True, TON)),
        ],
    )
    def test_wallet_status(self, user_id, row, expected):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchone.return_value = row
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(mod, "get_connection", return_value=conn):
            status = mod.get_user_wallet_status(user_id)
        assert (status["has_wallet"], status["wallet_address"]) == expected
        assert status["auth_method"] == row[0]


class TestAdminWalletCounts:
    def _run_summary(self):
        from api.routers.admin import stats

        cur = MagicMock()
        cur.fetchone.return_value = (3,)
        with patch.object(stats, "get_connection", return_value=_fake_conn(cur)):
            stats._admin_stats_visitors_summary_sync()
        return [c.args[0] for c in cur.execute.call_args_list]

    def test_summary_counts_evm_and_bound_wallets(self):
        sqls = self._run_summary()
        wallet_sqls = [s for s in sqls if "user_wallets" in s]
        # 錢包用戶總數＋近 7 日錢包訪客都要改用新定義
        assert len(wallet_sqls) >= 2
        assert all("evm_" in s for s in wallet_sqls)
        assert not any(
            "auth_method = 'ton_wallet'" in s and "user_wallets" not in s for s in sqls
        )

    def test_overview_counts_evm(self):
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1] / "api/routers/admin/stats.py"
        ).read_text(encoding="utf-8")
        assert "WHERE auth_method = 'ton_wallet'" not in src

    def test_visitor_list_has_wallet_uses_bindings(self):
        from api.routers.admin import stats

        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        cur = MagicMock()
        cur.fetchall.side_effect = [
            [(EVM_ID, now), ("tg_42", now)],
            [
                (EVM_ID, "EVM_3304e2", "evm_wallet", "free", now, now, True),
                ("tg_42", "TG_x", "telegram", "free", now, now, False),
            ],
            [],
        ]
        with patch.object(stats, "get_connection", return_value=_fake_conn(cur)):
            out = stats._admin_stats_visitors_list_sync(7, 50)
        flags = {v["user_id"]: v["has_wallet"] for v in out["visitors"]}
        assert flags == {EVM_ID: True, "tg_42": False}

    def test_user_detail_wallet(self):
        from api.routers.admin import users

        now = datetime(2026, 9, 25, tzinfo=timezone.utc)
        cur = MagicMock()
        cur.fetchone.side_effect = [
            (EVM_ID, "EVM_3304e2", "evm_wallet", "user", True, "free", None, now, now),
            (0,),
            (0,),
            (0,),
        ]
        cur.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        with (
            patch.object(users, "get_connection", return_value=conn),
            patch(
                "core.database.user.get_user_wallet_status",
                return_value={
                    "has_wallet": True,
                    "auth_method": "evm_wallet",
                    "wallet_address": EVM,
                },
            ),
        ):
            detail = users._get_user_detail_sync(EVM_ID)
        assert detail["has_wallet"] is True
        assert detail["wallet_address"] == EVM


# ──────────────────────────────────────────────────────────────────────────────
# A8：建帳預設值／暱稱保留字
# ──────────────────────────────────────────────────────────────────────────────


class TestCreateUserDefaults:
    def _create(self, identity, **kw):
        from core.database import user as mod

        cur = MagicMock()
        cur.fetchone.return_value = None
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(mod, "get_connection", return_value=conn):
            result = mod.create_or_get_user(identity, **kw)
        insert = next(
            c for c in cur.execute.call_args_list if "INSERT INTO users" in c.args[0]
        )
        return result, insert.args[1]

    @pytest.mark.parametrize(
        "identity, method, prefix",
        [
            (EVM_ID, "evm_wallet", "EVM_"),
            ("tg_42", "telegram", "TG_"),
            ("g_1089012345", "google", "G_"),
        ],
    )
    def test_defaults_follow_the_identity(self, identity, method, prefix):
        result, (_uid, username, auth_method) = self._create(identity)
        assert auth_method == method
        assert username.startswith(prefix)
        assert result["auth_method"] == method

    def test_unknown_identity_is_not_a_ton_wallet(self):
        _result, (_uid, username, auth_method) = self._create("test-user-009")
        assert auth_method != "ton_wallet"
        assert not username.startswith("TON_")

    def test_explicit_method_wins(self):
        _result, (_uid, username, auth_method) = self._create(
            "test-user-009", username="TestUser_009", auth_method="dev_test"
        )
        assert (username, auth_method) == ("TestUser_009", "dev_test")


class TestReservedDisplayNames:
    @pytest.fixture(autouse=True)
    def _no_rate_limit(self):
        from api.middleware.rate_limit import limiter

        with patch.object(limiter, "enabled", False):
            yield

    @pytest.mark.parametrize(
        "name",
        [
            "TON_abc",
            "EVM_3304e2",
            "TG_alice",
            "tg_alice",
            "G_123456",
            "USER_abcdef",
            "TestUser_001",
        ],
    )
    async def test_auto_generated_prefixes_are_reserved(
        self, client, auth_headers, name
    ):
        from api.routers import user as user_router

        with (
            patch.object(user_router, "is_display_name_taken", return_value=False),
            patch.object(user_router, "is_username_taken", return_value=False),
            patch.object(
                user_router, "set_user_display_name", return_value=(True, "ok")
            ),
        ):
            resp = await client.put(
                "/api/user/display-name",
                json={"display_name": name},
                headers=auth_headers,
            )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["reason"] == "reserved"

    def test_every_generated_prefix_is_reserved(self):
        """程式自動產生的 username 前綴都要在保留清單裡（新增前綴時漏加會紅）。"""
        from core.database.user import SYSTEM_USERNAME_PREFIXES, default_username

        for identity in (EVM_ID, "tg_42", "g_1089012345", TON, "test-user-009"):
            name = default_username(identity)
            assert name.upper().startswith(
                tuple(p.upper() for p in SYSTEM_USERNAME_PREFIXES)
            ), name


async def test_feedback_fallback_never_creates_ton_wallet_users(client):
    from api.deps import create_access_token
    from api.routers import user as user_router

    token = create_access_token(data={"sub": "tg_77"})
    created = MagicMock(return_value={"user_id": "tg_77"})
    with (
        patch(
            "api.deps.user_repo.get_by_id",
            new=AsyncMock(
                return_value={
                    "user_id": "tg_77",
                    "username": "",
                    "role": "user",
                    "is_active": True,
                }
            ),
        ),
        patch.object(user_router, "get_user_by_id", return_value=None),
        patch.object(user_router, "create_or_get_user", new=created),
        patch.object(user_router, "save_user_feedback", return_value=True),
    ):
        resp = await client.post(
            "/api/user-feedback",
            json={"message": "hello", "language": "en"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    args = created.call_args.args
    assert "ton_wallet" not in args


# ──────────────────────────────────────────────────────────────────────────────
# 舊 TON 身份：提醒綁 EVM／Google
# ──────────────────────────────────────────────────────────────────────────────


class TestLoginBackupFlag:
    async def _me(self, client, user_id, *, evm=(), google=None):
        from api.deps import create_access_token
        from api.routers import user as user_router

        user_router._ME_CACHE.clear()
        token = create_access_token(data={"sub": user_id})
        with (
            patch(
                "api.deps.user_repo.get_by_id",
                new=AsyncMock(
                    return_value={
                        "user_id": user_id,
                        "username": "u",
                        "role": "user",
                        "is_active": True,
                        "auth_method": "ton_wallet" if user_id == TON else "telegram",
                        "membership_tier": "free",
                        "has_wallet": True,
                    }
                ),
            ),
            patch.object(
                user_router.user_repo, "get_language", new=AsyncMock(return_value=None)
            ),
            patch.object(
                user_router.user_repo,
                "get_display_name",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                user_router.user_llm_preferences_repo,
                "get_selected_provider",
                new=AsyncMock(return_value=None),
            ),
            patch.object(
                user_router.user_wallet_repo,
                "bound_addresses",
                new=AsyncMock(return_value=list(evm)),
            ),
            patch("core.database.google.get_binding_by_user_id", return_value=google),
        ):
            resp = await client.get(
                "/api/user/me", headers={"Authorization": f"Bearer {token}"}
            )
        user_router._ME_CACHE.clear()
        assert resp.status_code == 200, resp.text
        return resp.json()["user"]

    async def test_legacy_ton_without_backup(self, client):
        assert (await self._me(client, TON))["login_backup_needed"] is True

    async def test_legacy_ton_with_evm_bound(self, client):
        assert (await self._me(client, TON, evm=[EVM]))["login_backup_needed"] is False

    async def test_legacy_ton_with_google(self, client):
        assert (await self._me(client, TON, google={"user_id": TON}))[
            "login_backup_needed"
        ] is False

    async def test_non_ton_users_never_flagged(self, client):
        assert (await self._me(client, "tg_42"))["login_backup_needed"] is False
