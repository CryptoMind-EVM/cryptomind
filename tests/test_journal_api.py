"""統一帳本 REST API 測試（PUT/DELETE /api/journal/entry）。

2026-08-24 盤點：update_entry/delete_entry 兩個端點零測試覆蓋——
「A 用戶改/刪 B 用戶帳目」的 ownership 防線（repo 以 token 主體的
user_id 構造 + SQL WHERE user_id）從未被測試鎖定。本檔補：
- auth：未登入 401
- ownership：repo 必須以 token 主體的 user_id 構造（跨用戶防線）
- 成功路徑：200 + 回應合約
- not-found：repo 說找不到 → 400
- 驗證邊界：Pydantic gt=0（amount <= 0 → 422）

對齊 test_scam_confirm_endpoint.py 的 patch DB 層風格（repo 層 fake）。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

USER_ID = "test-user-001"  # conftest.auth_headers 的 token 主體


class _FakeRepo:
    """捕捉 repo 構造與呼叫——驗證 ownership 防線與回應合約。"""

    constructed_uids: list = []
    last_instance = None

    def __init__(self, user_id, base_currency="TWD"):
        self.user_id = user_id
        self.calls: list = []
        _FakeRepo.constructed_uids.append(user_id)
        _FakeRepo.last_instance = self

    def add_entry(self, **kwargs):
        self.calls.append(("add_entry", kwargs))
        return {"ok": True, "id": 123}

    def update_entry(self, entry_id, **kwargs):
        self.calls.append(("update_entry", entry_id, kwargs))
        return {"ok": True, "id": entry_id}

    def delete_trade(self, trade_id):
        self.calls.append(("delete_trade", trade_id))
        return {"ok": True}

    def list_revisions(self, entry_id, limit=100):
        self.calls.append(("list_revisions", entry_id))
        return [{"revision_no": 1, "action": "create", "source": "chat"}]

    def restore_entry(self, entry_id, revision_no=None):
        self.calls.append(("restore_entry", entry_id, revision_no))
        return {"ok": True, "id": entry_id, "restored_fields": 2}

    def list_deleted(self, limit=50, offset=0):
        self.calls.append(("list_deleted", limit, offset))
        return [{"id": 9, "symbol": "BTC", "deleted_at": "2026-08-25T00:00:00+00:00"}]

    def list_trades(self, **kwargs):
        self.calls.append(("list_trades", kwargs))
        return [{"id": 1, "entry_type": "expense", "price": 100, "quantity": 1}]

    def get_entry_totals(self, **kwargs):
        self.calls.append(("get_entry_totals", kwargs))
        return {"expense": 100.0}

    def get_positions(self, price_lookup=None):
        self.calls.append(("get_positions", price_lookup))
        return [{
            "symbol": "BTC",
            "quantity": 0.5,
            "avg_cost": 100,
            "first_traded_at": "2026-01-01T00:00:00+00:00",
            "realized_pnl": 0,
        }]


@pytest.fixture(autouse=True)
def _reset_fake_repo():
    _FakeRepo.constructed_uids = []
    _FakeRepo.last_instance = None
    yield


@pytest.mark.integration
class TestJournalEntryApiAuth:
    """認證邊界：每個 data-mutating route 都必須拒絕未登入。

    conftest 全域 TEST_MODE=true 會讓 get_current_user 自動放行測試用戶，
    故 auth 測試必須在 TEST_MODE=False 下打（比照 test_chat_history_inheritance
    的 patch core.config.TEST_MODE 做法）——驗證的是 production 行為。
    """

    @pytest.mark.asyncio
    async def test_delete_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.delete("/api/journal/entry/1")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.put(
                "/api/journal/entry/1", json={"amount": 100}
            )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.post(
                "/api/journal/entry",
                json={"amount": 100, "currency": "TWD"},
            )
        assert response.status_code == 401


@pytest.mark.integration
class TestJournalEntryApiOwnership:
    """ownership 防線：repo 必須以 token 主體的 user_id 構造。

    跨用戶存取的實際攔截在 repo SQL（WHERE user_id = %s，見
    test_trade_journal.py 的 soft delete 契約測試）；本測試鎖定
    「API 層不把別人的 uid 餵進 repo」這一環。
    """

    @pytest.mark.asyncio
    async def test_delete_constructs_repo_with_caller_uid(self, client, auth_headers):
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.delete(
                "/api/journal/entry/42", headers=auth_headers
            )

        assert response.status_code == 200
        assert _FakeRepo.constructed_uids == [USER_ID], (
            "repo 必須以 token 主體的 user_id 構造（ownership 防線）"
        )
        assert _FakeRepo.last_instance.calls == [("delete_trade", 42)]

    @pytest.mark.asyncio
    async def test_update_constructs_repo_with_caller_uid(self, client, auth_headers):
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.put(
                "/api/journal/entry/42",
                json={"amount": 150, "note": "edited"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert _FakeRepo.constructed_uids == [USER_ID]
        kind, entry_id, kwargs = _FakeRepo.last_instance.calls[0]
        assert kind == "update_entry" and entry_id == 42
        assert kwargs["amount"] == 150
        assert kwargs["note"] == "edited"


@pytest.mark.integration
class TestJournalEntryApiContracts:
    """成功 / not-found / 回應合約。"""

    @pytest.mark.asyncio
    async def test_delete_success_contract(self, client, auth_headers):
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.delete(
                "/api/journal/entry/7", headers=auth_headers
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["deleted"] == 7

    @pytest.mark.asyncio
    async def test_delete_not_found_returns_400(self, client, auth_headers):
        class NotFoundRepo(_FakeRepo):
            def delete_trade(self, trade_id):
                return {"ok": False, "error": "entry not found"}

        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", NotFoundRepo
        ):
            response = await client.delete(
                "/api/journal/entry/999", headers=auth_headers
            )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_update_success_contract(self, client, auth_headers):
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.put(
                "/api/journal/entry/7",
                json={"category": "food"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["id"] == 7

    @pytest.mark.asyncio
    async def test_update_not_found_returns_400(self, client, auth_headers):
        class NotFoundRepo(_FakeRepo):
            def update_entry(self, entry_id, **kwargs):
                return {"ok": False, "error": "entry not found"}

        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", NotFoundRepo
        ):
            response = await client.put(
                "/api/journal/entry/999",
                json={"note": "x"},
                headers=auth_headers,
            )

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_database_error_maps_to_500(self, client, auth_headers):
        """基礎設施故障不得以 4xx 呈現——ops 告警以 5xx 為錨（2026-08-26 review）。"""

        class DbErrorRepo(_FakeRepo):
            def update_entry(self, entry_id, **kwargs):
                return {"ok": False, "error": "database error"}

        with patch("core.orm.trade_journal_repo.TradeJournalRepo", DbErrorRepo):
            response = await client.put(
                "/api/journal/entry/1", json={"note": "x"}, headers=auth_headers
            )
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_conflict_maps_to_409(self, client, auth_headers):
        """修訂號併發競態（UNIQUE 衝突）→ 409 可重試語義。"""

        class ConflictRepo(_FakeRepo):
            def update_entry(self, entry_id, **kwargs):
                return {"ok": False, "error": "conflict"}

        with patch("core.orm.trade_journal_repo.TradeJournalRepo", ConflictRepo):
            response = await client.put(
                "/api/journal/entry/1", json={"note": "x"}, headers=auth_headers
            )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_rate_unavailable_maps_to_400(self, client, auth_headers):
        class RateDownRepo(_FakeRepo):
            def update_entry(self, entry_id, **kwargs):
                return {"ok": False, "error": "rate unavailable"}

        with patch("core.orm.trade_journal_repo.TradeJournalRepo", RateDownRepo):
            response = await client.put(
                "/api/journal/entry/1",
                json={"currency": "EUR"},
                headers=auth_headers,
            )
        assert response.status_code == 400


@pytest.mark.integration
class TestJournalEntryApiValidation:
    """Pydantic 邊界驗證：金額必須 > 0（gt=0），拒絕 0 與負數。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_amount", [0, -1, -0.01])
    async def test_update_rejects_nonpositive_amount(
        self, client, auth_headers, bad_amount
    ):
        response = await client.put(
            "/api/journal/entry/1",
            json={"amount": bad_amount},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_nonpositive_amount(self, client, auth_headers):
        response = await client.post(
            "/api/journal/entry",
            json={"amount": 0, "currency": "TWD"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_accepts_partial_body(self, client, auth_headers):
        """全 Optional——空 body 仍可（只改 note 之類的部分更新）。"""
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.put(
                "/api/journal/entry/7", json={}, headers=auth_headers
            )
        assert response.status_code == 200


@pytest.mark.integration
class TestJournalEntryApiInjectionHardening:
    """邊界強化（2026-08-24 review）：幣別/長度/列舉/traded_at 格式。

    currency 直接前端 innerHTML 渲染——模式 ^[A-Za-z]+$ + max_length=8
    是 stored XSS 的伺服器側防線（前端另 escape，見 TestFrontendEscapeContract）。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_currency",
        [
            "<img src=x onerror=alert(1)>",  # HTML 注入
            "USD'",  # SQL 引號探測
            "A" * 9,  # 超過 max_length=8
            "",  # 空字串（min_length=1）
        ],
    )
    async def test_create_rejects_bad_currency(
        self, client, auth_headers, bad_currency
    ):
        response = await client.post(
            "/api/journal/entry",
            json={"amount": 100, "currency": bad_currency},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_rejects_html_currency(self, client, auth_headers):
        response = await client.put(
            "/api/journal/entry/1",
            json={"currency": "<script>alert(1)</script>"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_bad_entry_type(self, client, auth_headers):
        response = await client.post(
            "/api/journal/entry",
            json={"amount": 100, "entry_type": "gambling"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_oversized_note(self, client, auth_headers):
        response = await client.post(
            "/api/journal/entry",
            json={"amount": 100, "note": "x" * 201},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_create_rejects_malformed_traded_at(self, client, auth_headers):
        """非 ISO 8601 的 traded_at → 422（原為 500）。"""
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.post(
                "/api/journal/entry",
                json={"amount": 100, "traded_at": "not-a-date"},
                headers=auth_headers,
            )
        assert response.status_code == 422
        assert "ISO" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_accepts_valid_payload(self, client, auth_headers):
        """正常 payload 不被誤傷（含 Z 結尾 ISO 時間）。"""
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ):
            response = await client.post(
                "/api/journal/entry",
                json={
                    "entry_type": "expense",
                    "amount": 250,
                    "currency": "TWD",
                    "note": "lunch",
                    "traded_at": "2026-08-24T12:00:00Z",
                },
                headers=auth_headers,
            )
        assert response.status_code == 200
        assert response.json()["ok"] is True


@pytest.mark.integration
class TestJournalHistoryApi:
    """版本史三端點（c037）：auth / ownership / 合約 / 邊界。

    設計：docs/plans/2026-08-26-journal-version-history-design.md
    """

    @pytest.mark.asyncio
    async def test_revisions_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.get("/api/journal/entry/1/revisions")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_restore_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.post("/api/journal/entry/1/restore", json={})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_deleted_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.get("/api/journal/deleted")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_revisions_contract_and_ownership(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.get(
                "/api/journal/entry/42/revisions", headers=auth_headers
            )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["revisions"][0]["action"] == "create"
        assert _FakeRepo.constructed_uids == [USER_ID], (
            "repo 必須以 token 主體的 user_id 構造（ownership 防線）"
        )

    @pytest.mark.asyncio
    async def test_restore_passes_revision_no(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.post(
                "/api/journal/entry/42/restore",
                json={"revision_no": 2},
                headers=auth_headers,
            )
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert _FakeRepo.last_instance.calls[0] == ("restore_entry", 42, 2)

    @pytest.mark.asyncio
    async def test_restore_omitted_revision_no_passes_none(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.post(
                "/api/journal/entry/42/restore", json={}, headers=auth_headers
            )
        assert response.status_code == 200
        assert _FakeRepo.last_instance.calls[0] == ("restore_entry", 42, None)

    @pytest.mark.asyncio
    async def test_restore_rejects_nonpositive_revision_no(self, client, auth_headers):
        response = await client.post(
            "/api/journal/entry/1/restore",
            json={"revision_no": 0},
            headers=auth_headers,
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_restore_not_found_returns_400(self, client, auth_headers):
        class NotFoundRepo(_FakeRepo):
            def restore_entry(self, entry_id, revision_no=None):
                return {"ok": False, "error": "revision not found"}

        with patch("core.orm.trade_journal_repo.TradeJournalRepo", NotFoundRepo):
            response = await client.post(
                "/api/journal/entry/999/restore", json={}, headers=auth_headers
            )
        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_deleted_list_contract(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.get("/api/journal/deleted", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["entries"][0]["id"] == 9
        assert body["count"] == 1


@pytest.mark.integration
class TestJournalUxRound2Api:
    """UX 第二輪：entries 逗號子集篩選＋positions 端點。

    設計：docs/plans/2026-08-26-journal-ux-round2-design.md
    """

    @pytest.mark.asyncio
    async def test_entries_accepts_comma_subset(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.get(
                "/api/journal/entries?entry_type=expense,income",
                headers=auth_headers,
            )
        assert response.status_code == 200
        kind, kwargs = _FakeRepo.last_instance.calls[0]
        assert kind == "list_trades"
        assert kwargs["entry_type"] == ["expense", "income"]

    @pytest.mark.asyncio
    async def test_entries_single_value_still_works(self, client, auth_headers):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.get(
                "/api/journal/entries?entry_type=trade", headers=auth_headers
            )
        assert response.status_code == 200
        _, kwargs = _FakeRepo.last_instance.calls[0]
        assert kwargs["entry_type"] == ["trade"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["expense,hack", "all", "expense,"])
    async def test_entries_rejects_bad_entry_type(
        self, client, auth_headers, bad
    ):
        response = await client.get(
            f"/api/journal/entries?entry_type={bad}", headers=auth_headers
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_positions_requires_auth(self, client):
        with patch("core.config.TEST_MODE", False):
            response = await client.get("/api/journal/positions")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_positions_contract_and_ownership(self, client, auth_headers):
        with patch(
            "core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo
        ), patch(
            "core.tools.crypto_modules.trade_journal.build_price_lookup",
            lambda trades: {"BTC": 120},
        ):
            response = await client.get(
                "/api/journal/positions", headers=auth_headers
            )
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        assert body["positions"][0]["symbol"] == "BTC"
        assert _FakeRepo.constructed_uids == [USER_ID], (
            "repo 必須以 token 主體的 user_id 構造（ownership 防線）"
        )
        # 現價 lookup 有傳入 get_positions
        _, price_lookup = _FakeRepo.last_instance.calls[-1]
        assert price_lookup == {"BTC": 120}
