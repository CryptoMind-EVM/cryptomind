"""帳本行事曆改月曆（2026-09-12 DANNY：「做個類日曆，點日期看當日事件」）：
API 區間參數、標記、facade 登記、i18n 四語、node 純函式斷言。"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


def _client():
    from api.routers import calendar as mod

    app = FastAPI()
    app.state.limiter = mod.limiter
    app.include_router(mod.router)

    async def fake_user():
        return {"user_id": "u1"}

    app.dependency_overrides[mod.get_current_user] = fake_user
    try:
        mod.limiter.reset()
    except Exception:  # noqa: BLE001
        pass
    return TestClient(app), mod


class TestRangeApi:
    def test_explicit_range_is_passed_to_store(self):
        client, mod = _client()
        with patch.object(mod.store, "list_events_between", return_value=[]) as lst:
            res = client.get("/api/journal/calendar?start=2026-08-30&end=2026-10-10")
        assert res.status_code == 200, res.text
        assert res.json()["start"] == "2026-08-30" and res.json()["end"] == "2026-10-10"
        lst.assert_called_once_with("u1", date(2026, 8, 30), date(2026, 10, 10))

    def test_default_stays_today_plus_days(self):
        client, mod = _client()
        with patch.object(mod.store, "list_events_between", return_value=[]) as lst:
            res = client.get("/api/journal/calendar?days=7")
        assert res.status_code == 200
        args = lst.call_args.args
        assert args[0] == "u1" and (args[2] - args[1]).days == 7

    def test_range_validation(self):
        client, mod = _client()
        with patch.object(mod.store, "list_events_between", return_value=[]):
            assert (
                client.get("/api/journal/calendar?start=2026-09-01").status_code == 422
            )
            assert (
                client.get(
                    "/api/journal/calendar?start=2026-09-10&end=2026-09-01"
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/journal/calendar?start=2026-01-01&end=2026-12-31"
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/journal/calendar?start=2026-09-01&end=2026-09-01"
                ).status_code
                == 200
            )


def test_markup_and_facade_wired():
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    for needle in (
        'id="journal-calendar-month"',
        'id="journal-calendar-weekdays"',
        'id="journal-calendar-grid" class="grid grid-cols-7',
        'id="journal-calendar-day-title"',
        'id="journal-calendar-list"',
        'data-click="Journal.calendarPrev"',
        'data-click="Journal.calendarNext"',
        'data-click="Journal.calendarToday"',
        'data-click="Journal.addCalendarEvent"',
        'id="journal-calendar-date" type="date"',
    ):
        assert needle in html, needle
    js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(
        encoding="utf-8"
    )
    assert "from '../journal-calendar.js'" in js
    for name in ("calendarSelect", "calendarPrev", "calendarNext", "calendarToday"):
        assert f"    {name}:" in js, (
            f"window.Journal 外觀物件漏登記 {name}（白名單，漏了按鈕靜默沒反應）"
        )
    assert 'data-click="Journal.calendarSelect" data-click-arg="${c.iso}"' in js
    assert (
        "AppAPI.get(`/api/journal/calendar?start=${grid.start}&end=${grid.end}`)" in js
    )
    # 新增事件後跳到那一天（可能跨月）
    assert "JournalState.calendarSelected = eventDate;" in js
    css = (REPO / "web" / "css" / "tailwind-built.css").read_text(encoding="utf-8")
    for cls in (
        "grid-cols-7",
        "sm\\:min-h-14",
        "bg-primary\\/15",
        "ring-primary",
        "bg-primary\\/40",
    ):
        assert f".{cls}" in css, (
            f"tailwind-built.css 缺 {cls}（要跑 npm run build:css）"
        )


@pytest.mark.parametrize("lang", ["en", "zh-TW", "zh-CN", "ru"])
def test_locales(lang):
    d = json.loads(
        (REPO / "web" / "js" / "i18n" / f"{lang}.json").read_text(encoding="utf-8")
    )
    cal = d["journal"]["calendar"]
    for key in (
        "prevMonth",
        "nextMonth",
        "today",
        "dayCount",
        "noDayEvents",
        "kindEarnings",
        "kindReport",
        "kindUnlock",
        "kindMacro",
        "kindCustom",
    ):
        assert key in cal, f"{lang} 缺 journal.calendar.{key}"
    assert "{n}" in cal["dayCount"]
    assert "kr_stock" in d["journal"]["units"] and "cn_stock" in d["journal"]["units"]
    ui = json.loads(
        (REPO / "core" / "i18n" / "ui_messages.json").read_text(encoding="utf-8")
    )
    assert "daily_brief.sys_cn_report" in ui[lang]


def test_node_gate_passes():
    try:
        subprocess.run(["node", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("node 不在 PATH")
    proc = subprocess.run(
        ["node", str(REPO / "tests" / "js" / "journal_calendar.mjs")],
        capture_output=True,
        text=True,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "journal_calendar: ok" in proc.stderr


def test_calendar_is_its_own_view_not_buried_in_investments():
    """2026-09-30 DANNY：「日曆在哪裡我怎麼沒看到」——以前塞在投資持倉最底下（五張卡之後），
    收支總覽完全看不到。改成帳本的第三個視圖：收支總覽｜投資持倉｜行事曆。"""
    html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
    inv = html[html.index('id="journal-invest-view"') : html.index('id="journal-calendar-view"')]
    assert 'id="journal-calendar"' not in inv, "行事曆不能再放在投資持倉視圖裡"
    cal = html[html.index('id="journal-calendar-view"') :]
    assert cal.index('id="journal-calendar"') < cal.index('id="journal-form-modal"')
    assert 'data-click="Journal.switchView" data-click-arg="calendar"' in html
    assert "grid grid-cols-3" in html[html.index("子視圖切換") :][:400], "手機三顆鈕一排"
    js = (REPO / "web" / "js" / "components" / "tab-journal.js").read_text(encoding="utf-8")
    refresh = js.split("async _refresh() {")[1].split("\n    },")[0]
    assert "JournalState.view === 'calendar'" in refresh and "_loadCalendar()" in refresh
    assert "journal-calendar-view" in js
