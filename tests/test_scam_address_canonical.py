"""scam_reports 的 TON 地址：新資料存 raw、查詢對得到舊的 upper() 寫法、舊列自我修復。

舊版把地址整串 .upper() 存。EVM 無妨（hex 不分大小寫），TON friendly 是 base64——
upper 後解不回來，而且同一個錢包的 EQ／UQ upper 後是兩串，用另一種寫法查就漏。
"""

from __future__ import annotations

import contextlib
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from core.onchain.addresses import (
    scam_address_key,
    scam_address_lookup_keys,
    ton_friendly_to_raw,
    ton_raw_to_friendly,
)
from core.validators import mask_wallet_address

pytestmark = pytest.mark.unit

# USDT jetton master——同一個帳戶的三種寫法
EQ = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
UQ = "UQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_p0p"
RAW = "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
# 大寫後跟 EQ 一模一樣、CRC 也合法，但是**另一個帳戶**（隨機翻大小寫幾萬次就湊得出來）
VARIANT = "EQCxe6MUTqjkFnGFArotkOT1LZbdIix1KciXrv7nw2id_SDS"
EVM = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


# ─── 地址寫法 ────────────────────────────────────────────────────────────────


class TestAddressForms:
    def test_raw_to_friendly_matches_known_forms(self):
        assert ton_raw_to_friendly(RAW, bounceable=True) == EQ
        assert ton_raw_to_friendly(RAW) == UQ  # 預設 non-bounceable（錢包慣用顯示）
        assert ton_raw_to_friendly(EQ) == UQ  # 任何寫法都收
        assert ton_friendly_to_raw(UQ) == RAW

    @pytest.mark.parametrize("bad", ["", "   ", EVM, "EQshort", EQ.upper(), "0:xyz"])
    def test_raw_to_friendly_invalid_returns_none(self, bad):
        assert ton_raw_to_friendly(bad) is None

    def test_variant_is_a_different_account_with_same_upper(self):
        assert VARIANT.upper() == EQ.upper()
        assert ton_friendly_to_raw(VARIANT) not in (None, RAW)

    @pytest.mark.parametrize("form", [EQ, UQ, RAW, RAW.upper(), f"  {UQ} "])
    def test_storage_key_for_ton_is_raw(self, form):
        assert scam_address_key(form) == RAW

    @pytest.mark.parametrize(
        "addr", [EVM, EVM.lower(), "G" + "a" * 55, "some-other-chain-address-xyz"]
    )
    def test_storage_key_for_non_ton_is_upper_as_before(self, addr):
        assert scam_address_key(addr) == addr.strip().upper()

    def test_lookup_keys_start_with_storage_key_and_cover_legacy_forms(self):
        keys = scam_address_lookup_keys(UQ)
        assert keys[0] == RAW
        legacy = set(keys[1:])
        assert {EQ.upper(), UQ.upper(), RAW.upper()} <= legacy
        # testnet（kQ／0Q）與標準 base64（+／）舊 API 也收過
        assert ton_raw_to_friendly(RAW, testnet=True).upper() in legacy
        assert ton_raw_to_friendly(RAW, bounceable=True, testnet=True).upper() in legacy
        std = ton_raw_to_friendly(RAW, bounceable=True, url_safe=False)
        assert std != EQ and std.upper() in legacy

    def test_lookup_keys_identical_for_every_form(self):
        expected = scam_address_lookup_keys(RAW)
        for form in (EQ, UQ, RAW.upper(), f" {EQ} "):
            assert scam_address_lookup_keys(form) == expected

    def test_lookup_keys_for_evm_unchanged(self):
        assert scam_address_lookup_keys(EVM) == [EVM.upper()]
        assert scam_address_lookup_keys(EVM.lower()) == [EVM.upper()]


# ─── Repo（fake session；SQL／參數構造）──────────────────────────────────────


class _Result:
    def __init__(self, value=None, rows=None, rowcount=0):
        self._value = value
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def unique(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, results=(), flush_error=None, execute_error=None):
        self._results = list(results)
        self._flush_error = flush_error
        self._execute_error = execute_error
        self.statements = []
        self.added = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        if self._execute_error is not None:
            raise self._execute_error
        return self._results.pop(0) if self._results else _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self._flush_error is not None:
            raise self._flush_error
        for obj in self.added:
            obj.id = 42

    async def refresh(self, obj):
        return None

    def begin_nested(self):
        @contextlib.asynccontextmanager
        async def _nested():
            yield

        return _nested()


def _unique_violation():
    return IntegrityError(
        "INSERT", {}, Exception("duplicate key value violates unique constraint")
    )


@pytest.fixture
def fake_session(monkeypatch):
    # core.orm 的 __init__ 把 scam_tracker_repo 名稱綁成實例，要拿模組本身
    mod = importlib.import_module("core.orm.scam_tracker_repo")

    def _install(session):
        @contextlib.asynccontextmanager
        async def _using(session_arg=None):
            yield session

        monkeypatch.setattr(mod, "using_session", _using)
        return session

    return _install


def _params(stmt) -> dict:
    return stmt.compile(dialect=postgresql.dialect()).params


def _flat_param_values(stmt) -> list:
    out = []
    for v in _params(stmt).values():
        out.extend(v if isinstance(v, (list, tuple)) else [v])
    return out


def _create_kwargs(addr):
    return dict(
        scam_wallet_address=addr,
        reporter_user_id="u1",
        reporter_wallet_masked="EQab…cd",
        scam_type="phishing",
        description="d" * 30,
    )


class TestRepo:
    async def test_create_report_stores_ton_as_raw(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_FakeSession(results=[_Result(value=None)]))
        result = await scam_tracker_repo.create_report(**_create_kwargs(f" {UQ} "))
        assert result == {"success": True, "report_id": 42}
        assert s.added[0].scam_wallet_address == RAW

    async def test_create_report_keeps_evm_upper(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_FakeSession(results=[_Result(value=None)]))
        await scam_tracker_repo.create_report(**_create_kwargs(EVM))
        assert s.added[0].scam_wallet_address == EVM.upper()

    async def test_create_report_duplicate_across_formats(self, fake_session):
        """舊列存 EQ.upper()，用 UQ 再報 → already_reported（以前 unique 對不到，會重複建一筆）。"""
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_FakeSession(results=[_Result(value=7)]))
        result = await scam_tracker_repo.create_report(**_create_kwargs(UQ))
        assert result == {
            "success": False,
            "error": "already_reported",
            "existing_report_id": 7,
        }
        assert s.added == []
        assert EQ.upper() in _flat_param_values(s.statements[0])

    async def test_create_report_unique_race_returns_already_reported(
        self, fake_session
    ):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(
            _FakeSession(
                results=[_Result(value=None), _Result(value=9)],
                flush_error=_unique_violation(),
            )
        )
        result = await scam_tracker_repo.create_report(**_create_kwargs(EQ))
        assert result == {
            "success": False,
            "error": "already_reported",
            "existing_report_id": 9,
        }
        assert len(s.statements) == 2  # 撞 unique 後重查一次

    async def test_find_reports_matches_every_legacy_form(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_FakeSession(results=[_Result(rows=[])]))
        assert await scam_tracker_repo.find_reports_by_address(EQ) == []
        values = _flat_param_values(s.statements[0])
        for key in scam_address_lookup_keys(EQ):
            assert key in values

    async def test_heal_rewrites_legacy_row_to_raw(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        s = fake_session(_FakeSession(results=[_Result(rowcount=1)]))
        assert (
            await scam_tracker_repo.heal_legacy_address(3, EQ.upper(), RAW) == "healed"
        )
        sql = str(s.statements[0].compile(dialect=postgresql.dialect()))
        assert sql.startswith("UPDATE scam_reports SET scam_wallet_address=")
        values = _flat_param_values(s.statements[0])
        assert 3 in values and EQ.upper() in values and RAW in values

    async def test_heal_skipped_when_row_already_changed(self, fake_session):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        fake_session(_FakeSession(results=[_Result(rowcount=0)]))
        assert (
            await scam_tracker_repo.heal_legacy_address(3, EQ.upper(), RAW) == "skipped"
        )

    async def test_heal_unique_conflict_keeps_legacy_row(self, fake_session):
        """raw 列已存在 → 不刪不併，舊列原樣留著。"""
        from core.orm.scam_tracker_repo import scam_tracker_repo

        fake_session(_FakeSession(execute_error=_unique_violation()))
        assert (
            await scam_tracker_repo.heal_legacy_address(3, EQ.upper(), RAW)
            == "conflict"
        )


# ─── 舉報人隱私：reporter_wallet_masked 對外一律遮罩 ─────────────────────────


def _row(stored, reporter_wallet):
    return SimpleNamespace(
        id=1,
        scam_wallet_address=stored,
        scam_type="phishing",
        description="d" * 30,
        transaction_hash=None,
        verification_status="pending",
        approve_count=0,
        reject_count=0,
        comment_count=0,
        view_count=0,
        reporter_wallet_masked=reporter_wallet,
        created_at=None,
        updated_at=None,
        reporter=None,
    )


_FULL_REPORTER_WALLETS = [EQ, UQ, EVM, "G" + "B" * 55, RAW]


class TestReporterPrivacy:
    @pytest.mark.parametrize("full", _FULL_REPORTER_WALLETS)
    def test_dict_builders_mask_legacy_full_address(self, full):
        """ORM 建立路徑以前把完整地址直接寫進這欄；舊列不改 DB，輸出時補遮。"""
        from core.orm.scam_tracker_repo import (
            _report_detail_to_dict,
            _report_row_to_dict,
        )

        for build in (_report_row_to_dict, _report_detail_to_dict):
            shown = build(_row(RAW, full))["reporter_wallet_masked"]
            assert shown == mask_wallet_address(full)
            assert full not in shown

    @pytest.mark.parametrize(
        "masked", [mask_wallet_address(EQ), "EQab…cd", "GABC...XYZ", "PTEST***", ""]
    )
    def test_already_masked_value_passes_through(self, masked):
        from core.orm.scam_tracker_repo import (
            _report_detail_to_dict,
            _report_row_to_dict,
        )

        for build in (_report_row_to_dict, _report_detail_to_dict):
            assert build(_row(RAW, masked))["reporter_wallet_masked"] == masked

    @pytest.mark.parametrize("full", _FULL_REPORTER_WALLETS)
    async def test_list_search_detail_never_return_full_address(
        self, fake_session, full
    ):
        from core.orm.scam_tracker_repo import scam_tracker_repo

        fake_session(_FakeSession(results=[_Result(rows=[_row(RAW, full)])]))
        listed = await scam_tracker_repo.get_reports()
        fake_session(_FakeSession(results=[_Result(rows=[_row(RAW, full)])]))
        found = await scam_tracker_repo.find_reports_by_address(EQ)
        fake_session(_FakeSession(results=[_Result(rows=[_row(RAW, full)])]))
        detail = await scam_tracker_repo.get_report_by_id(1, increment_view=False)
        for report in (listed[0], found[0], detail):
            assert report["reporter_wallet_masked"] == mask_wallet_address(full)
            assert full not in report["reporter_wallet_masked"]


# ─── Router：查詢、自我修復、顯示 ───────────────────────────────────────────


def _report(report_id, stored):
    return {
        "id": report_id,
        "scam_wallet_address": stored,
        "scam_type": "phishing",
        "description": "d" * 30,
        "transaction_hash": None,
        "verification_status": "pending",
        "approve_count": 0,
        "reject_count": 0,
        "comment_count": 0,
        "view_count": 0,
        "reporter_wallet_masked": "EQab…cd",
        "created_at": None,
        "updated_at": None,
        "reporter_username": None,
        "net_votes": 0,
        "viewer_vote": None,
    }


@pytest.fixture
def client():
    from api.routers.scam_tracker import router as scam_router

    app = FastAPI()
    app.include_router(scam_router)
    return TestClient(app)


_REPO = "api.routers.scam_tracker.reports.scam_tracker_repo"
# 修復不該打任何網路（以前會查 TonAPI active；friendly 舊列現在一律不改）
_NO_NETWORK = "core.onchain.ton_source._get"


def _search(client, addr):
    return client.get(
        "/api/scam-tracker/reports/search", params={"wallet_address": addr}
    )


class TestRouterLookups:
    @pytest.mark.parametrize("stored", [EQ.upper(), UQ.upper()])
    @pytest.mark.parametrize("query", [EQ, UQ, RAW])
    def test_friendly_legacy_row_found_by_every_form_and_never_rewritten(
        self, client, query, stored
    ):
        """真正詐騙地址的 EQ／UQ／raw 都查得到 friendly 舊列；舊列不改寫。"""
        assert stored in scam_address_lookup_keys(query)
        heal = AsyncMock(return_value="healed")
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(1, stored)]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
            patch(_NO_NETWORK, side_effect=AssertionError("不該打 TonAPI")),
        ):
            resp = _search(client, query)
        assert resp.status_code == 200
        assert resp.json()["found"] is True
        assert resp.json()["report"]["id"] == 1
        heal.assert_not_awaited()

    def test_active_collision_variant_cannot_rewrite_legacy_row(self, client):
        """upper() 後撞在一起的是另一個帳戶（就算它 active）——不能把舊列搬過去。"""
        assert EQ.upper() in scam_address_lookup_keys(VARIANT)
        heal = AsyncMock(return_value="healed")
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(1, EQ.upper())]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
            patch(_NO_NETWORK, return_value={"status": "active"}),
        ):
            _search(client, VARIANT)
        heal.assert_not_awaited()
        # 舊列原封不動，真正詐騙地址的三種寫法照樣對得到
        for form in (EQ, UQ, RAW):
            assert EQ.upper() in scam_address_lookup_keys(form)

    @pytest.mark.parametrize("query", [EQ, UQ, RAW])
    def test_raw_upper_legacy_row_healed(self, client, query):
        """raw 的 hex 不分大小寫，upper() 過也沒有歧義 → 改寫成 raw。"""
        heal = AsyncMock(return_value="healed")
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(1, RAW.upper())]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
            patch(_NO_NETWORK, side_effect=AssertionError("不該打 TonAPI")),
        ):
            _search(client, query)
        heal.assert_awaited_once_with(1, RAW.upper(), RAW)

    def test_conflict_keeps_both_rows_in_results(self, client):
        heal = AsyncMock(return_value="conflict")
        rows = [_report(1, RAW), _report(2, RAW.upper()), _report(3, EQ.upper())]
        with (
            patch(f"{_REPO}.find_reports_by_address", new=AsyncMock(return_value=rows)),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
        ):
            resp = _search(client, EQ)
        body = resp.json()
        assert resp.status_code == 200
        assert [r["id"] for r in body["reports"]] == [1, 2, 3]
        assert body["report"]["id"] == 1
        heal.assert_awaited_once_with(2, RAW.upper(), RAW)

    def test_heal_error_does_not_break_lookup(self, client):
        heal = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(1, RAW.upper())]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
        ):
            resp = _search(client, EQ)
        assert resp.status_code == 200
        assert resp.json()["found"] is True

    def test_raw_row_displayed_as_non_bounceable(self, client):
        heal = AsyncMock()
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(1, RAW)]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
        ):
            body = _search(client, EQ).json()
        assert body["report"]["scam_wallet_address"] == UQ
        heal.assert_not_awaited()

    def test_evm_lookup_unchanged(self, client):
        heal = AsyncMock()
        find = AsyncMock(return_value=[_report(1, EVM.upper())])
        with (
            patch(f"{_REPO}.find_reports_by_address", new=find),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
        ):
            body = _search(client, EVM).json()
        assert body["report"]["scam_wallet_address"] == EVM.upper()
        heal.assert_not_awaited()

    def test_not_found(self, client):
        with patch(f"{_REPO}.find_reports_by_address", new=AsyncMock(return_value=[])):
            body = _search(client, UQ).json()
        assert body["found"] is False

    def test_list_and_detail_display_friendly(self, client):
        rows = [_report(1, RAW), _report(2, EVM.upper()), _report(3, EQ.upper())]
        with patch(f"{_REPO}.get_reports", new=AsyncMock(return_value=rows)):
            listed = client.get("/api/scam-tracker/reports").json()["reports"]
        assert [r["scam_wallet_address"] for r in listed] == [
            UQ,
            EVM.upper(),
            EQ.upper(),
        ]
        with patch(
            f"{_REPO}.get_report_by_id", new=AsyncMock(return_value=_report(1, RAW))
        ):
            detail = client.get("/api/scam-tracker/reports/1").json()["report"]
        assert detail["scam_wallet_address"] == UQ

    def test_check_uses_normalized_lookup_and_report_id(self, client):
        from types import SimpleNamespace

        heal = AsyncMock(return_value="healed")
        safety = SimpleNamespace(
            verification="none",
            symbol=None,
            name=None,
            holders_count=0,
            has_admin=False,
            exists=True,
            signals={},
        )
        with (
            patch(
                f"{_REPO}.find_reports_by_address",
                new=AsyncMock(return_value=[_report(5, RAW.upper())]),
            ),
            patch(f"{_REPO}.heal_legacy_address", new=heal),
            patch(
                "core.tools.crypto_modules.ton_safety.assess_jetton_safety",
                return_value=safety,
            ),
        ):
            resp = client.get("/api/scam-tracker/reports/check", params={"address": UQ})
        community = resp.json()["sources"]["community"]
        assert community["found"] is True
        assert community["report_id"] == 5
        assert resp.json()["verdict"] == "high_risk"
        heal.assert_awaited_once_with(5, RAW.upper(), RAW)


class TestCreateDuplicate:
    @pytest.fixture(autouse=True)
    def _pass_create_guards(self):
        """Premium／每日上限在路由先擋（見 test_scam_report_create_guards）；這裡都放行。"""
        with (
            patch(f"{_REPO}.count_reports_today", new=AsyncMock(return_value=0)),
            patch(
                "api.routers.scam_tracker.reports.config_repo.get_config",
                new=AsyncMock(return_value=5),
            ),
            patch(
                "api.routers.scam_tracker.reports._recompute_trust_on_scam_hit",
                new=AsyncMock(),
            ),
        ):
            yield

    def test_duplicate_report_returns_409_with_readable_message(self, client):
        from api.deps import get_current_user

        client.app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
        create = AsyncMock(
            return_value={
                "success": False,
                "error": "already_reported",
                "existing_report_id": 7,
            }
        )
        with (
            patch(f"{_REPO}.create_report", new=create),
            patch(
                "api.routers.scam_tracker.reports.user_repo.get_by_id",
                new=AsyncMock(return_value={"user_id": "u1", "is_premium": True}),
            ),
        ):
            resp = client.post(
                "/api/scam-tracker/reports",
                json={
                    "scam_wallet_address": UQ,
                    "reporter_wallet_address": EQ,
                    "scam_type": "phishing",
                    "description": "d" * 30,
                },
            )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["existing_report_id"] == 7
        # 前端顯示 detail.message（detail 是物件時沒有 message 會顯示 [object Object]）
        assert isinstance(detail["message"], str) and detail["message"]

    def test_create_stores_masked_reporter_wallet(self, client):
        from api.deps import get_current_user

        client.app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1"}
        create = AsyncMock(return_value={"success": True, "report_id": 11})
        with (
            patch(f"{_REPO}.create_report", new=create),
            patch(
                "api.routers.scam_tracker.reports.user_repo.get_by_id",
                new=AsyncMock(return_value={"user_id": "u1", "is_premium": True}),
            ),
        ):
            resp = client.post(
                "/api/scam-tracker/reports",
                json={
                    "scam_wallet_address": UQ,
                    "reporter_wallet_address": f" {EQ} ",
                    "scam_type": "phishing",
                    "description": "d" * 30,
                },
            )
        assert resp.status_code == 200
        stored = create.await_args.kwargs["reporter_wallet_masked"]
        assert stored == mask_wallet_address(EQ)
        assert EQ not in stored


# ─── 信任分數的詐騙命中 ──────────────────────────────────────────────────────


def test_trust_scam_penalty_matches_any_form():
    from core.identity import scoring

    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    with patch.object(scoring, "get_connection", return_value=conn):
        result = scoring.collect_scam_penalty(UQ)
    assert result["reason"] == "scam_report_hit"
    sql, params = cursor.execute.call_args.args
    assert "ANY(%s)" in sql
    assert params[0] == scam_address_lookup_keys(UQ)
    assert RAW in params[0] and EQ.upper() in params[0]
