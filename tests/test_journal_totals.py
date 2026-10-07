"""帳本總支出／總收入要算篩選範圍內的全部條目（2026-09-25 盤查）。

根因：tab-journal.js 的摘要卡拿 /api/journal/entries 回的列表加總，而列表
limit=100——超過 100 筆的使用者總額少算。

修法：entries 端點一起回 totals（同一組篩選、不分頁的 SQL 合計），
前端用它；舊回應沒有 totals 才退回列表加總。前端行為在
tests/js/frontend_feature_bugs.mjs 的 3b2 段。
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

import core.orm.trade_journal_repo as repo_module

FILTERS = dict(
    entry_type=["expense", "income"],
    category="food",
    symbol=None,
    search="lunch",
    date_from="2026-09-01T00:00:00",
    date_to="2026-09-30T23:59:59",
)


def _capture(monkeypatch, rows):
    captured: list = []

    def fake_query_all(sql, params=None):
        captured.append((sql, list(params or [])))
        return rows

    monkeypatch.setattr(
        repo_module.DatabaseBase, "query_all", staticmethod(fake_query_all)
    )
    return captured


def _where(sql: str) -> str:
    body = sql.split("WHERE", 1)[1]
    for end in ("GROUP BY", "ORDER BY"):
        body = body.split(end, 1)[0]
    return " ".join(body.split())


@pytest.mark.unit
class TestEntryTotalsRepo:
    def test_totals_sum_every_matching_entry_by_type(self, monkeypatch):
        captured = _capture(
            monkeypatch,
            [
                {"entry_type": "expense", "total": Decimal("123456.78")},
                {"entry_type": "income", "total": Decimal("5000")},
            ],
        )

        totals = repo_module.TradeJournalRepo("u1").get_entry_totals(**FILTERS)

        assert totals == {"expense": 123456.78, "income": 5000.0}
        sql, _ = captured[0]
        assert "LIMIT" not in sql, "合計不能受列表分頁限制"
        assert "GROUP BY entry_type" in sql
        assert "deleted_at IS NULL" in sql, "已刪條目不能算進合計"
        # 同前端原本的口徑：有換算值用換算值，沒有才用 price x quantity
        assert "converted_amount > 0" in sql

    def test_totals_use_exactly_the_list_filters(self, monkeypatch):
        """摘要卡與下面的列表是同一組篩選——兩邊條件漂移就對不上。"""
        captured = _capture(monkeypatch, [])
        repo = repo_module.TradeJournalRepo("u1")

        repo.list_trades(**FILTERS, limit=100, offset=0)
        repo.get_entry_totals(**FILTERS)

        (list_sql, list_params), (total_sql, total_params) = captured
        assert _where(total_sql) == _where(list_sql)
        assert total_params == list_params[:-2]  # 去掉 LIMIT／OFFSET

    def test_totals_empty_when_no_rows(self, monkeypatch):
        _capture(monkeypatch, [])
        assert repo_module.TradeJournalRepo("u1").get_entry_totals() == {}


class _FakeRepo:
    last = None

    def __init__(self, user_id, base_currency="TWD"):
        self.calls: list = []
        _FakeRepo.last = self

    def list_trades(self, **kwargs):
        self.calls.append(("list_trades", kwargs))
        return [{"id": 1, "entry_type": "expense", "price": 100, "quantity": 1}]

    def get_entry_totals(self, **kwargs):
        self.calls.append(("get_entry_totals", kwargs))
        return {"expense": 25000.0, "income": 3000.0}


@pytest.mark.integration
class TestEntriesEndpointTotals:
    @pytest.mark.asyncio
    async def test_entries_response_carries_totals_for_same_filters(
        self, client, auth_headers
    ):
        with patch("core.orm.trade_journal_repo.TradeJournalRepo", _FakeRepo):
            response = await client.get(
                "/api/journal/entries?entry_type=expense,income&category=food"
                "&search=lunch&limit=100",
                headers=auth_headers,
            )

        assert response.status_code == 200
        assert response.json()["totals"] == {"expense": 25000.0, "income": 3000.0}
        calls = dict(_FakeRepo.last.calls)
        list_kwargs = dict(calls["list_trades"])
        list_kwargs.pop("limit")
        list_kwargs.pop("offset")
        assert calls["get_entry_totals"] == list_kwargs
