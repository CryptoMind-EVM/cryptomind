"""行事曆：重複事件（每週／每月／每年）與編輯（2026-09-24）。

重複事件只存一列（錨點日＝event_date），讀取時在區間內展開成多筆；
早報提醒、聊天工具、月曆都走 ``list_events_between``，所以三條路徑一起生效。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.daily_brief import store

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "u1", "username": "u", "membership_tier": "free"}


def _row(event_date, recurrence="none", until=None, **kw):
    return {
        "id": kw.pop("id", 1),
        "event_date": event_date,
        "title": kw.pop("title", "rent"),
        "kind": "custom",
        "symbol": None,
        "market": None,
        "source": "user",
        "remind_days_before": 1,
        "note": "",
        "recurrence": recurrence,
        "recurrence_until": until,
        **kw,
    }


def _dates(rows):
    return [r["event_date"] for r in rows]


class TestExpand:
    def test_one_off_inside_and_outside_range(self):
        r = _row(date(2026, 9, 15))
        assert _dates(
            store.expand_occurrences(r, date(2026, 9, 1), date(2026, 9, 30))
        ) == [date(2026, 9, 15)]
        assert store.expand_occurrences(r, date(2026, 10, 1), date(2026, 10, 31)) == []

    def test_weekly(self):
        r = _row(date(2026, 9, 1), "weekly")
        out = store.expand_occurrences(r, date(2026, 9, 10), date(2026, 9, 30))
        assert _dates(out) == [date(2026, 9, 15), date(2026, 9, 22), date(2026, 9, 29)]

    def test_monthly_clamps_to_month_end(self):
        r = _row(date(2026, 1, 31), "monthly")
        out = store.expand_occurrences(r, date(2026, 2, 1), date(2026, 4, 30))
        assert _dates(out) == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]

    def test_yearly_leap_day(self):
        r = _row(date(2024, 2, 29), "yearly")
        out = store.expand_occurrences(r, date(2026, 1, 1), date(2026, 12, 31))
        assert _dates(out) == [date(2026, 2, 28)]
        out = store.expand_occurrences(r, date(2028, 1, 1), date(2028, 12, 31))
        assert _dates(out) == [date(2028, 2, 29)]

    def test_until_is_inclusive(self):
        r = _row(date(2026, 9, 5), "monthly", until=date(2026, 11, 5))
        out = store.expand_occurrences(r, date(2026, 9, 1), date(2026, 12, 31))
        assert _dates(out) == [date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5)]

    def test_nothing_before_anchor(self):
        r = _row(date(2026, 12, 1), "weekly")
        assert store.expand_occurrences(r, date(2026, 9, 1), date(2026, 11, 30)) == []

    def test_occurrence_keeps_id_and_series_start(self):
        r = _row(date(2026, 9, 5), "monthly", id=42)
        out = store.expand_occurrences(r, date(2026, 10, 1), date(2026, 10, 31))
        assert out == [
            {**r, "event_date": date(2026, 10, 5), "series_start": date(2026, 9, 5)}
        ]
        assert r["event_date"] == date(2026, 9, 5), "不能改到傳進來的 row"


class TestListEventsBetween:
    def test_expands_and_sorts(self):
        rows = [
            _row(date(2026, 9, 20), id=1, title="one-off"),
            _row(date(2026, 8, 10), "monthly", id=2, title="card bill"),
        ]
        with patch.object(store.DatabaseBase, "query_all", return_value=rows) as q:
            out = store.list_events_between("u1", date(2026, 9, 1), date(2026, 10, 31))
        sql, params = q.call_args.args
        assert "recurrence" in sql and "recurrence_until" in sql
        assert params[0] == "u1"
        assert [(e["id"], e["event_date"]) for e in out] == [
            (2, date(2026, 9, 10)),
            (1, date(2026, 9, 20)),
            (2, date(2026, 10, 10)),
        ]


class _FakeCursor:
    def __init__(self, row, description):
        self.row = row
        self.description = description
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class _Col:
    def __init__(self, name):
        self.name = name


class TestUpdateEvent:
    def _run(self, row):
        cols = [_Col(n) for n in ("id", "event_date", "title")]
        cur = _FakeCursor(row, cols)

        class Conn:
            def cursor(self):
                return cur

        @contextmanager
        def fake_tx():
            yield Conn()

        with patch("core.database.base.transaction", fake_tx):
            out = store.update_event(
                "u1",
                7,
                event_date=date(2026, 10, 5),
                title="card bill",
                remind_days_before=2,
                note="",
                recurrence="monthly",
                recurrence_until=None,
                dedupe_key="user:2026-10-05:card bill",
            )
        return out, cur

    def test_only_user_events_are_editable(self):
        out, cur = self._run((7, date(2026, 10, 5), "card bill"))
        sql, params = cur.executed[0]
        assert "UPDATE user_calendar_events" in sql
        assert "source = 'user'" in sql, "系統事件每天同步會蓋回去，不給改"
        assert params[-2:] == ("u1", 7)
        assert out == {"id": 7, "event_date": date(2026, 10, 5), "title": "card bill"}

    def test_missing_returns_none(self):
        out, _ = self._run(None)
        assert out is None


def _client():
    from api.routers import calendar as cal

    app = FastAPI()
    app.include_router(cal.router)

    async def fake_user():
        return USER

    app.dependency_overrides[cal.get_current_user] = fake_user
    return TestClient(app)


class TestApi:
    def test_add_passes_recurrence(self):
        target = (date.today() + timedelta(days=5)).isoformat()
        with patch("core.daily_brief.store.add_event", return_value={"id": 9}) as add:
            res = _client().post(
                "/api/journal/calendar",
                json={"event_date": target, "title": "rent", "recurrence": "monthly"},
            )
        assert res.status_code == 201
        assert add.call_args.kwargs["recurrence"] == "monthly"
        assert add.call_args.kwargs["recurrence_until"] is None

    def test_add_rejects_bad_recurrence_and_until(self):
        target = date.today() + timedelta(days=5)
        with patch("core.daily_brief.store.add_event") as add:
            c = _client()
            assert (
                c.post(
                    "/api/journal/calendar",
                    json={
                        "event_date": target.isoformat(),
                        "title": "x",
                        "recurrence": "daily",
                    },
                ).status_code
                == 422
            )
            assert (
                c.post(
                    "/api/journal/calendar",
                    json={
                        "event_date": target.isoformat(),
                        "title": "x",
                        "recurrence": "monthly",
                        "recurrence_until": (target - timedelta(days=1)).isoformat(),
                    },
                ).status_code
                == 400
            )
        add.assert_not_called()

    def test_until_dropped_for_one_off(self):
        target = (date.today() + timedelta(days=5)).isoformat()
        with patch("core.daily_brief.store.add_event", return_value={"id": 9}) as add:
            _client().post(
                "/api/journal/calendar",
                json={
                    "event_date": target,
                    "title": "x",
                    "recurrence_until": "2099-01-01",
                },
            )
        assert add.call_args.kwargs["recurrence_until"] is None

    def test_update(self):
        target = (date.today() + timedelta(days=3)).isoformat()
        saved = {
            "id": 7,
            "event_date": date.today() + timedelta(days=3),
            "title": "card bill",
            "kind": "custom",
            "source": "user",
            "remind_days_before": 2,
            "note": "",
            "recurrence": "monthly",
            "recurrence_until": None,
        }
        with patch("core.daily_brief.store.update_event", return_value=saved) as upd:
            res = _client().put(
                "/api/journal/calendar/7",
                json={
                    "event_date": target,
                    "title": "  card   bill ",
                    "remind_days_before": 2,
                    "recurrence": "monthly",
                },
            )
        assert res.status_code == 200
        body = res.json()["event"]
        assert body["recurrence"] == "monthly" and body["title"] == "card bill"
        args, kw = upd.call_args
        assert args == ("u1", 7)
        assert kw["title"] == "card bill" and kw["recurrence"] == "monthly"
        assert kw["dedupe_key"] == f"user:{target}:card bill"

    def test_update_allows_past_series_start(self):
        """編輯重複事件時送回的是錨點日，可能早就過了。"""
        past = (date.today() - timedelta(days=90)).isoformat()
        with patch("core.daily_brief.store.update_event", return_value={"id": 7}):
            res = _client().put(
                "/api/journal/calendar/7",
                json={"event_date": past, "title": "rent", "recurrence": "monthly"},
            )
        assert res.status_code == 200

    def test_update_not_found_and_duplicate(self):
        target = (date.today() + timedelta(days=3)).isoformat()
        body = {"event_date": target, "title": "x"}
        with patch("core.daily_brief.store.update_event", return_value=None):
            assert (
                _client().put("/api/journal/calendar/7", json=body).status_code == 404
            )
        with patch(
            "core.daily_brief.store.update_event",
            side_effect=store.DuplicateEventError(),
        ):
            assert (
                _client().put("/api/journal/calendar/7", json=body).status_code == 409
            )

    def test_list_serializes_recurrence(self):
        rows = [
            {
                **_row(date(2026, 10, 5), "monthly", until=date(2027, 3, 5), id=3),
                "series_start": date(2026, 9, 5),
            }
        ]
        with patch("core.daily_brief.store.list_events_between", return_value=rows):
            ev = _client().get("/api/journal/calendar?days=30").json()["events"][0]
        assert ev["event_date"] == "2026-10-05"
        assert ev["recurrence"] == "monthly"
        assert ev["recurrence_until"] == "2027-03-05"
        assert ev["series_start"] == "2026-09-05"


class TestTool:
    def test_add_monthly(self):
        from core.tools.calendar_tool import add_calendar_event

        target = (date.today() + timedelta(days=3)).isoformat()
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.add_event", return_value={"id": 5}) as add,
        ):
            out = json.loads(
                add_calendar_event.invoke(
                    {
                        "event_date": target,
                        "title": "card bill",
                        "recurrence": "monthly",
                    }
                )
            )
        assert out["ok"] is True and out["recurrence"] == "monthly"
        assert add.call_args.kwargs["recurrence"] == "monthly"

    def test_add_rejects_unknown_recurrence(self):
        from core.tools.calendar_tool import add_calendar_event

        target = (date.today() + timedelta(days=3)).isoformat()
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.add_event") as add,
        ):
            out = add_calendar_event.invoke(
                {"event_date": target, "title": "x", "recurrence": "hourly"}
            )
        assert out.startswith("Error")
        add.assert_not_called()

    def test_list_shows_recurrence(self):
        from core.tools.calendar_tool import list_calendar_events

        rows = [_row(date.today() + timedelta(days=2), "weekly", id=4)]
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.list_events_between", return_value=rows),
        ):
            out = json.loads(list_calendar_events.invoke({"days": 14}))
        assert out["events"][0]["recurrence"] == "weekly"


class TestWiring:
    def test_migration_and_schema(self):
        mig = (REPO / "alembic" / "versions" / "c052_calendar_recurrence.py").read_text(
            encoding="utf-8"
        )
        assert 'revision = "c052"' in mig and 'down_revision = "c051"' in mig
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        for col in ("recurrence ", "recurrence_until "):
            assert col in mig and col in schema, col
        assert (
            '"ALTER TABLE user_calendar_events ADD COLUMN IF NOT EXISTS "' in schema
        ), "既有 DB 要自癒補欄位"
        assert "\"recurrence TEXT NOT NULL DEFAULT 'none'\"" in schema

    def test_ui_wired(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert 'id="journal-calendar-repeat"' in html
        assert 'id="journal-calendar-cancel"' in html
        js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(
            encoding="utf-8"
        )
        assert "AppAPI.put(`/api/journal/calendar/${" in js
        facade = js[js.index("window.Journal = {") :]
        assert "editCalendarEvent: (id) => JournalTab.editCalendarEvent(id)" in facade
        assert "cancelCalendarEdit: () => JournalTab.cancelCalendarEdit()" in facade

    def test_brief_reminds_recurring_occurrence(self):
        """早報的提醒窗吃展開後的日期：每月 5 號的事件，10/4 要提醒。"""
        today = date(2026, 10, 4)
        rows = store.expand_occurrences(
            _row(date(2026, 9, 5), "monthly"), today, today + timedelta(days=7)
        )
        assert [r["event_date"] for r in rows] == [date(2026, 10, 5)]
