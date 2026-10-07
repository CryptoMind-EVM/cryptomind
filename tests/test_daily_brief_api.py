"""每日早報偏好 API 與 bot 端開關（不打 DB）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

USER = {"user_id": "u_prem", "username": "prem", "membership_tier": "premium"}


def _row(**kw):
    base = {
        "user_id": "u_prem",
        "language": "zh-TW",
        "display_name": "DANNY",
        "membership_tier": "premium",
        "telegram_id": 111,
        "prefs_user_id": None,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "last_sent_on": None,
    }
    base.update(kw)
    return base


def _client():
    from api.routers import brief as brief_module

    app = FastAPI()
    app.include_router(brief_module.router)

    async def fake_user():
        return USER

    app.dependency_overrides[brief_module.get_current_user] = fake_user
    return TestClient(app)


class TestPrefsApi:
    def test_get_returns_defaults_when_no_row(self):
        client = _client()
        with patch("core.daily_brief.store.get_prefs_row", return_value=_row()):
            res = client.get("/api/user/brief-prefs")
        assert res.status_code == 200
        prefs = res.json()["prefs"]
        assert prefs["enabled"] is True and prefs["send_hour"] == 8
        assert prefs["timezone"] == "Asia/Taipei" and prefs["telegram_bound"] is True
        assert prefs["channels"] == ["telegram", "inapp", "baseapp"]

    def test_put_saves_and_echoes(self):
        client = _client()
        saved = {}

        def fake_upsert(user_id, **kw):
            saved["user_id"] = user_id
            saved.update(kw)

        with (
            patch("core.daily_brief.store.upsert_prefs", side_effect=fake_upsert),
            patch(
                "core.daily_brief.store.get_prefs_row",
                return_value=_row(
                    prefs_user_id="u_prem",
                    enabled=False,
                    send_hour=7,
                    timezone="Europe/London",
                    channels=["inapp"],
                    include_spend=False,
                    last_sent_on=date(2026, 9, 12),
                ),
            ),
        ):
            res = client.put(
                "/api/user/brief-prefs",
                json={
                    "enabled": False,
                    "send_hour": 7,
                    "timezone": "Europe/London",
                    "channels": ["inapp", "inapp", "bogus"],
                    "include_spend": False,
                    "include_macro": False,
                },
            )
        assert res.status_code == 200, res.text
        assert saved == {
            "user_id": "u_prem",
            "enabled": False,
            "send_hour": 7,
            "timezone": "Europe/London",
            "channels": ["inapp"],
            "include_spend": False,
            "include_macro": False,
        }
        assert res.json()["prefs"]["last_sent_on"] == "2026-09-12"

    def test_put_rejects_bad_timezone_hour_and_empty_channels(self):
        client = _client()
        with patch("core.daily_brief.store.upsert_prefs") as up:
            assert (
                client.put(
                    "/api/user/brief-prefs",
                    json={"enabled": True, "timezone": "Mars/Olympus"},
                ).status_code
                == 422
            )
            assert (
                client.put(
                    "/api/user/brief-prefs", json={"enabled": True, "send_hour": 24}
                ).status_code
                == 422
            )
            assert (
                client.put(
                    "/api/user/brief-prefs",
                    json={"enabled": True, "channels": ["bogus"]},
                ).status_code
                == 422
            )
        up.assert_not_called()

    def test_preview_builds_text_without_sending(self):
        from datetime import date as _date

        from core.daily_brief.compose import BriefData

        client = _client()
        data = BriefData(
            language="zh-TW",
            date_local=_date(2026, 9, 13),
            alerts=[{"title": "x", "body": "BTC < 60,000"}],
        )

        async def fake_collect(prefs, now):
            return data

        with (
            patch("core.daily_brief.store.get_prefs_row", return_value=_row()),
            patch("core.daily_brief.collect.collect_brief_data", new=fake_collect),
            patch("core.daily_brief.store.mark_sent") as mark,
            patch("core.daily_brief.send.send_telegram_text") as tg,
        ):
            res = client.post("/api/user/brief-prefs/preview")
        assert res.status_code == 200
        assert "BTC < 60,000" in res.json()["text"] and res.json()["empty"] is False
        mark.assert_not_called()
        tg.assert_not_called()


class TestBotToggle:
    def _client(self):
        from api.routers import telegram_link as tl

        app = FastAPI()
        app.include_router(tl.router)
        app.dependency_overrides[tl._require_bot_secret] = lambda: None
        return TestClient(app), tl

    def test_bot_toggle_requires_binding(self):
        client, tl = self._client()
        with patch.object(tl, "get_binding_by_telegram_id", return_value=None):
            res = client.post(
                "/api/telegram/brief", json={"telegram_id": 1, "enabled": True}
            )
        assert res.status_code == 403

    def test_bot_toggle_sets_enabled_with_language_timezone(self):
        client, tl = self._client()
        with (
            patch.object(
                tl,
                "get_binding_by_telegram_id",
                return_value={"telegram_id": 1, "user_id": "u_prem"},
            ),
            patch("core.database.user.get_user_language", return_value="en"),
            patch("core.daily_brief.store.set_enabled") as set_enabled,
        ):
            res = client.post(
                "/api/telegram/brief", json={"telegram_id": 1, "enabled": False}
            )
        assert res.status_code == 200 and res.json() == {
            "success": True,
            "enabled": False,
        }
        set_enabled.assert_called_once_with("u_prem", False, timezone="UTC")


def test_settings_wiring():
    """Settings 卡、模組載入、delegator 白名單、init hook 四處都要接上。"""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    settings = (repo / "web" / "js" / "components" / "tab-settings.js").read_text(
        encoding="utf-8"
    )
    for needle in (
        'id="settings-brief-card"',
        'data-click="saveBriefPrefs"',
        'data-click="previewBrief"',
        'id="brief-hour"',
        'id="brief-timezone"',
    ):
        assert needle in settings, needle
    spa = (repo / "web" / "js" / "spa.js").read_text(encoding="utf-8")
    assert "import('./brief-settings.js')" in spa
    assert "window.loadBriefPrefs()" in spa
    delegator = (repo / "web" / "js" / "click-delegator.js").read_text(encoding="utf-8")
    assert "'saveBriefPrefs'" in delegator and "'previewBrief'" in delegator
    server = (repo / "api_server.py").read_text(encoding="utf-8")
    assert "app.include_router(brief_router)" in server
