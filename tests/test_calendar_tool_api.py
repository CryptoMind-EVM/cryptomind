"""行事曆：聊天工具（add／list）、REST API、與接線守衛。"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.tools.calendar_tool import (
    add_calendar_event,
    list_calendar_events,
    parse_event_date,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "u1", "username": "u", "membership_tier": "free"}


class TestParseDate:
    def test_formats(self):
        today = date(2026, 9, 12)
        assert parse_event_date("2026-09-15", today) == date(2026, 9, 15)
        assert parse_event_date("2026/9/15", today) == date(2026, 9, 15)
        assert parse_event_date("9/15", today) == date(2026, 9, 15)
        # 沒給年且已過 → 明年
        assert parse_event_date("01-02", today) == date(2027, 1, 2)
        assert parse_event_date("", today) is None
        assert parse_event_date("2026-13-01", today) is None
        assert parse_event_date("tomorrow", today) is None


class TestTool:
    def test_add_requires_login(self):
        with patch("core.tools.calendar_tool._get_current_user_id", return_value=None):
            out = add_calendar_event.invoke(
                {"event_date": "2026-09-15", "title": "房租"}
            )
        assert out.startswith("Error: login required")

    def test_add_saves_user_event_with_dedupe_key(self):
        saved = {}

        def fake_add(uid, **kw):
            saved["uid"] = uid
            saved.update(kw)
            return {"id": 7}

        target = (date.today() + timedelta(days=3)).isoformat()
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.add_event", side_effect=fake_add),
        ):
            out = json.loads(
                add_calendar_event.invoke(
                    {
                        "event_date": target,
                        "title": "  房租   繳費 ",
                        "remind_days_before": 99,
                    }
                )
            )
        assert out["ok"] is True and out["id"] == 7 and out["title"] == "房租 繳費"
        assert out["remind_days_before"] == 30, "提前天數要夾在 0..30"
        assert saved["source"] == "user" and saved["kind"] == "custom"
        assert saved["dedupe_key"] == f"user:{target}:房租 繳費"

    def test_add_rejects_bad_or_out_of_range_dates(self):
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.add_event") as add,
        ):
            assert add_calendar_event.invoke(
                {"event_date": "nope", "title": "x"}
            ).startswith("Error: cannot parse")
            far = (date.today() + timedelta(days=400)).isoformat()
            assert "out of range" in add_calendar_event.invoke(
                {"event_date": far, "title": "x"}
            )
            assert add_calendar_event.invoke(
                {"event_date": "2099-01-01", "title": "   "}
            ).startswith("Error")
        add.assert_not_called()

    def test_list_returns_json_events(self):
        rows = [
            {
                "id": 1,
                "event_date": date.today() + timedelta(days=2),
                "title": "NVDA 財報",
                "source": "system",
                "kind": "earnings",
                "symbol": "NVDA",
            },
        ]
        with (
            patch("core.tools.calendar_tool._get_current_user_id", return_value="u1"),
            patch("core.daily_brief.store.list_events_between", return_value=rows),
        ):
            out = json.loads(list_calendar_events.invoke({"days": 500}))
        assert out["days"] == 120 and out["count"] == 1
        assert (
            out["events"][0]["title"] == "NVDA 財報"
            and out["events"][0]["source"] == "system"
        )


def _client():
    from api.routers import calendar as cal

    app = FastAPI()
    app.include_router(cal.router)

    async def fake_user():
        return USER

    app.dependency_overrides[cal.get_current_user] = fake_user
    return TestClient(app)


class TestApi:
    def test_list(self):
        rows = [
            {
                "id": 3,
                "event_date": date(2026, 9, 15),
                "title": "房租",
                "source": "user",
                "kind": "custom",
                "remind_days_before": 1,
            }
        ]
        with patch("core.daily_brief.store.list_events_between", return_value=rows):
            res = _client().get("/api/journal/calendar?days=30")
        assert res.status_code == 200
        assert res.json()["events"][0] == {
            "id": 3,
            "event_date": "2026-09-15",
            "title": "房租",
            "kind": "custom",
            "symbol": None,
            "market": None,
            "source": "user",
            "remind_days_before": 1,
            "note": "",
            "recurrence": "none",
            "recurrence_until": None,
            "series_start": "2026-09-15",
        }

    def test_add_and_validation(self):
        target = (date.today() + timedelta(days=5)).isoformat()
        with patch(
            "core.daily_brief.store.add_event",
            return_value={
                "id": 9,
                "event_date": target,
                "title": "房租",
                "source": "user",
                "kind": "custom",
                "remind_days_before": 2,
            },
        ) as add:
            res = _client().post(
                "/api/journal/calendar",
                json={"event_date": target, "title": " 房租 ", "remind_days_before": 2},
            )
        assert res.status_code == 201 and res.json()["event"]["id"] == 9
        assert (
            add.call_args.kwargs["title"] == "房租"
            and add.call_args.kwargs["source"] == "user"
        )
        with patch("core.daily_brief.store.add_event") as add2:
            assert (
                _client()
                .post(
                    "/api/journal/calendar",
                    json={"event_date": "2020-01-01", "title": "x"},
                )
                .status_code
                == 400
            )
            assert (
                _client()
                .post(
                    "/api/journal/calendar",
                    json={"event_date": target, "title": "", "remind_days_before": 1},
                )
                .status_code
                == 422
            )
            assert (
                _client()
                .post(
                    "/api/journal/calendar",
                    json={"event_date": target, "title": "x", "remind_days_before": 31},
                )
                .status_code
                == 422
            )
        add2.assert_not_called()

    def test_delete(self):
        with patch("core.daily_brief.store.delete_event", return_value=True):
            assert _client().delete("/api/journal/calendar/5").status_code == 200
        with patch("core.daily_brief.store.delete_event", return_value=False):
            assert _client().delete("/api/journal/calendar/5").status_code == 404


class TestWiring:
    def test_tools_registered_seeded_and_translated(self):
        bootstrap = (REPO / "core" / "agents" / "bootstrap.py").read_text(
            encoding="utf-8"
        )
        seed = (REPO / "core" / "database" / "tools.py").read_text(encoding="utf-8")
        trans = (REPO / "core" / "agents" / "tool_name_translations.py").read_text(
            encoding="utf-8"
        )
        for name in ("add_calendar_event", "list_calendar_events"):
            assert f'name="{name}"' in bootstrap, f"bootstrap 沒註冊 {name}"
            assert f'"tool_id": "{name}"' in seed, f"seed 沒有 {name}"
            assert f'"{name}": {{' in trans, f"tool_name_translations 缺 {name}"
        # cryptomind 預設清單要看得到（2026-08-22 帳本工具就漏過一次）
        block = seed[seed.index('"cryptomind": [') :]
        block = block[: block.index("]")]
        assert "add_calendar_event" in block and "list_calendar_events" in block

