"""新手三步完成狀態（PR-7）：``GET /api/user/onboarding-status``。

完成狀態全部從既有資料推（不新增表）：
- holding：帳本有未刪除的紀錄（鏈上同步寫進來的也算）；單純「有綁錢包」不算——
  EVM 登入會自動寫 user_wallets，每個 EVM 使用者都會直接打勾
- brief：生效的早報偏好開著、而且有送得到的管道
- call：有沒取消的明確喊單
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

USER = {"user_id": "u_new", "username": "new"}
WEB = Path(__file__).resolve().parents[1] / "web" / "js"


@pytest.fixture(autouse=True)
def _reset_limiter():
    from api.middleware.rate_limit import limiter

    limiter.reset()
    yield
    limiter.reset()


def _prefs_row(**kw):
    base = {
        "user_id": "u_new",
        "language": "en",
        "display_name": None,
        "membership_tier": "free",
        "telegram_id": None,
        "prefs_user_id": None,
        "enabled": None,
        "send_hour": None,
        "timezone": None,
        "channels": None,
        "include_spend": None,
        "include_macro": None,
        "last_sent_on": None,
        # c063（#956）：只有使用者自己設定過的偏好列才算數，系統自動建的照預設
        "user_set": None,
    }
    base.update(kw)
    return base


def _app(override: bool = True):
    from api.routers import onboarding as mod

    app = FastAPI()
    app.include_router(mod.router)
    if override:

        async def fake_user():
            return USER

        app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app)


def _status(counts: dict, prefs_row):
    from core import onboarding

    with (
        patch.object(onboarding.DatabaseBase, "query_one", return_value=counts) as q,
        patch("core.daily_brief.store.get_prefs_row", return_value=prefs_row),
    ):
        out = onboarding.onboarding_status("u_new")
    return out, q


class TestCoreStatus:
    def test_brand_new_user_has_nothing_done(self):
        out, _ = _status({"has_entry": False, "has_call": False}, _prefs_row())
        assert out == {
            "holding": False,
            "brief": False,
            "call": False,
            "telegram_bound": False,
        }

    def test_all_done(self):
        out, _ = _status(
            {"has_entry": True, "has_call": True}, _prefs_row(telegram_id=42)
        )
        assert out == {
            "holding": True,
            "brief": True,  # 綁 TG、沒偏好列＝預設開
            "call": True,
            "telegram_bound": True,
        }

    def test_inapp_brief_counts_without_telegram(self):
        out, _ = _status(
            {"has_entry": False, "has_call": False},
            _prefs_row(prefs_user_id="u_new", enabled=True, channels=["inapp"], user_set=True),
        )
        assert out["brief"] is True and out["telegram_bound"] is False

    def test_telegram_only_channel_without_binding_is_not_done(self):
        out, _ = _status(
            {"has_entry": False, "has_call": False},
            _prefs_row(prefs_user_id="u_new", enabled=True, channels=["telegram"]),
        )
        assert out["brief"] is False

    def test_explicitly_disabled_brief_is_not_done(self):
        out, _ = _status(
            {"has_entry": False, "has_call": False},
            _prefs_row(prefs_user_id="u_new", enabled=False, telegram_id=42, user_set=True),
        )
        assert out["brief"] is False and out["telegram_bound"] is True

    def test_query_is_parameterized_and_checks_soft_delete_and_cancel(self):
        _, q = _status({"has_entry": False, "has_call": False}, _prefs_row())
        sql, params = q.call_args.args
        assert params == ("u_new", "u_new")
        assert "u_new" not in sql and sql.count("%s") == 2
        assert "deleted_at IS NULL" in sql
        assert "cancelled" in sql

    def test_missing_row_is_all_false(self):
        out, _ = _status(None, None)
        assert out == {
            "holding": False,
            "brief": False,
            "call": False,
            "telegram_bound": False,
        }


class TestEndpoint:
    def test_requires_login(self):
        client = _app(override=False)
        with patch("core.config.TEST_MODE", False):
            res = client.get("/api/user/onboarding-status")
        assert res.status_code == 401

    def test_returns_three_booleans_for_current_user(self):
        seen = {}

        def fake_status(user_id):
            seen["user_id"] = user_id
            return {
                "holding": True,
                "brief": False,
                "call": False,
                "telegram_bound": False,
            }

        with patch("core.onboarding.onboarding_status", side_effect=fake_status):
            res = _app().get("/api/user/onboarding-status")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["status"] == {
            "holding": True,
            "brief": False,
            "call": False,
            "telegram_bound": False,
        }
        assert seen["user_id"] == "u_new"

    def test_failure_does_not_leak_internals(self):
        with patch(
            "core.onboarding.onboarding_status",
            side_effect=RuntimeError("password=hunter2 relation does not exist"),
        ):
            res = _app().get("/api/user/onboarding-status")
        assert res.status_code == 500
        assert "hunter2" not in res.text and "relation" not in res.text


class TestFrontendWiring:
    """前端接線：清單掛在登入歡迎畫面、按鈕在委派白名單、文案四語都有。"""

    def _src(self, name):
        return (WEB / name).read_text(encoding="utf-8")

    def test_welcome_screen_hooks_the_checklist_for_logged_in_users_only(self):
        src = self._src("chat-sessions.js")
        hook = [
            ln for ln in src.splitlines() if "import('./onboarding-checklist.js')" in ln
        ]
        assert len(hook) == 1 and "isLoggedIn()" in hook[0]

    def test_checklist_actions_are_dispatchable(self):
        src = self._src("onboarding-checklist.js")
        assert "onclick=" not in src.lower()
        assert "'OnboardingChecklist'" in self._src("click-delegator.js")
        methods = set(re.findall(r'data-click="OnboardingChecklist\.(\w+)"', src))
        assert methods == {
            "dismiss",
            "openJournal",
            "openBriefSettings",
            "connectTelegram",
        }
        for m in methods:
            assert f"    {m}(" in src or f"    async {m}(" in src

    def test_checklist_copy_exists_in_every_language(self):
        src = self._src("onboarding-checklist.js")
        keys = set(re.findall(r"_t\(\s*'(onboarding\.[\w.]+)'", src))
        keys.add("onboarding.callExamplePrompt")  # fillChatExample 的 data-click-arg
        assert len(keys) >= 15
        for lang in ("zh-TW", "zh-CN", "en", "ru"):
            catalog = json.loads((WEB / "i18n" / f"{lang}.json").read_text("utf-8"))
            for key in keys:
                node = catalog
                for part in key.split("."):
                    node = node[part]
                assert isinstance(node, str) and node, (lang, key)

    def test_telegram_connect_opens_the_deep_link(self):
        src = self._src("telegram-link.js")
        assert "data.deep_link" in src and "openTelegramLink" in src
        assert "startsWith('https://t.me/')" in src
        # 回到分頁立刻重查綁定狀態、綁好通知清單
        assert "visibilitychange" in src and "telegram:bound" in src
