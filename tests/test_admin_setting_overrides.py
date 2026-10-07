"""後台覆寫（core/setting_overrides.py、c055、PUT/DELETE /api/admin/settings-center/overrides）。

全部 mock：不打 DB、不連 Redis。
"""

from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import setting_overrides as so

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _fresh_cache():
    so._cache.update({"data": {}, "at": 0.0, "loaded": False})
    yield
    so._cache.update({"data": {}, "at": 0.0, "loaded": False})


def _with_overrides(data):
    """讓 get() 讀到這份覆寫（等同 Redis 裡的 settings:overrides）。"""
    return patch("core.shared_cache.get_json", side_effect=lambda k: data if k == so.REDIS_KEY else None)


class TestWhitelist:
    def test_security_and_import_time_flags_are_not_overridable(self):
        for name in (
            "PII_SCRUB_ENABLED",
            "AUTH_LOCKOUT_ENABLED",
            "CONSENT_TOOL_GUARD_ENABLED",
            "MCP_ENABLED",
            "WALLET_MONITOR_ENABLED",
            "TRUST_SCORE_ENABLED",
            "MULTICHAIN_ENABLED",
            "AGENT_PRESETS_ENABLED",
        ):
            assert not so.is_overridable(name), name

    def test_overridable_flags_are_registered_and_read_at_call_time(self):
        from core.feature_flags import FLAG_REGISTRY

        config_src = (REPO / "core" / "config.py").read_text(encoding="utf-8")
        for name in so.OVERRIDABLE_FLAGS:
            assert name in FLAG_REGISTRY, name
            # core/config.py 的 import 時常數切了不會生效，不能放進白名單
            assert not re.search(rf"^{name}\s*=", config_src, re.M), name

    def test_overridable_params_are_shown_in_settings_center(self):
        from core.admin_settings import PARAM_REGISTRY

        assert set(so.OVERRIDABLE_PARAMS) <= set(PARAM_REGISTRY)


class TestValidate:
    @pytest.mark.parametrize("raw,out", [("true", "true"), ("ON", "true"), ("0", "false"), ("off", "false")])
    def test_flag_values(self, raw, out):
        assert so.validate("VISION_ENABLED", raw) == out

    def test_bad_flag_value(self):
        with pytest.raises(so.InvalidOverride):
            so.validate("VISION_ENABLED", "maybe")

    def test_param_bounds(self):
        assert so.validate("FREE_DAILY_CHAT_LIMIT", " 30 ") == "30"
        with pytest.raises(so.InvalidOverride):
            so.validate("FREE_DAILY_CHAT_LIMIT", "-1")
        with pytest.raises(so.InvalidOverride):
            so.validate("GUEST_DAILY_QUESTIONS", "abc")
        with pytest.raises(so.InvalidOverride):
            so.validate("GUEST_DAILY_QUESTIONS", "100000")

    def test_not_overridable(self):
        with pytest.raises(so.InvalidOverride):
            so.validate("PII_SCRUB_ENABLED", "false")


class TestRead:
    def test_get_uses_cache_and_ttl(self):
        with _with_overrides({"VISION_ENABLED": "false"}) as get_json:
            assert so.get("VISION_ENABLED") == "false"
            assert so.get("VISION_ENABLED") == "false"
            assert get_json.call_count == 1, "TTL 內不重讀 Redis"
            so.invalidate_local()
            so.get("VISION_ENABLED")
            assert get_json.call_count == 2

    def test_non_whitelisted_key_never_reads(self):
        with _with_overrides({"PII_SCRUB_ENABLED": "false"}) as get_json:
            assert so.get("PII_SCRUB_ENABLED") is None
        get_json.assert_not_called()

    def test_redis_failure_keeps_last_value(self):
        with _with_overrides({"VISION_ENABLED": "false"}):
            assert so.get("VISION_ENABLED") == "false"
        so.invalidate_local()
        with patch("core.shared_cache.get_json", side_effect=RuntimeError("redis down")):
            assert so.get("VISION_ENABLED") == "false"


class TestHooks:
    def test_env_flag_prefers_override(self, monkeypatch):
        from core.feature_flags import env_flag

        monkeypatch.setenv("VISION_ENABLED", "true")
        with _with_overrides({"VISION_ENABLED": "false"}):
            assert env_flag("VISION_ENABLED", "true") is False
        so.invalidate_local()
        with _with_overrides({}):
            assert env_flag("VISION_ENABLED", "true") is True

    def test_env_flag_ignores_override_for_other_flags(self, monkeypatch):
        from core.feature_flags import env_flag

        monkeypatch.setenv("MCP_ENABLED", "false")
        with _with_overrides({"MCP_ENABLED": "true"}):
            assert env_flag("MCP_ENABLED") is False

    def test_product_getters_follow_override(self):
        from api.vision import vision_enabled
        from core.feature_flags import email_brief_enabled, reown_social_login_enabled

        with _with_overrides(
            {"VISION_ENABLED": "false", "EMAIL_BRIEF_ENABLED": "false", "REOWN_SOCIAL_LOGIN_ENABLED": "false"}
        ):
            assert vision_enabled() is False
            assert email_brief_enabled() is False
            assert reown_social_login_enabled() is False

    def test_daily_brief_switch_keeps_deny_list(self, monkeypatch):
        from core.feature_flags import daily_brief_enabled

        monkeypatch.delenv("DAILY_BRIEF_ENABLED", raising=False)
        with _with_overrides({"DAILY_BRIEF_ENABLED": "false"}):
            assert daily_brief_enabled() is False
        so.invalidate_local()
        with _with_overrides({}):
            assert daily_brief_enabled() is True
        src = (REPO / "scripts" / "cron_daily_brief.py").read_text(encoding="utf-8")
        assert "return daily_brief_enabled()" in src

    def test_numeric_params_follow_override(self, monkeypatch):
        from api.routers.guest import _daily_limit, _global_cap
        from core.agents.fallback import fallback_daily_limit

        monkeypatch.setenv("GUEST_DAILY_QUESTIONS", "3")
        with _with_overrides(
            {"GUEST_DAILY_QUESTIONS": "7", "GUEST_GLOBAL_DAILY_CAP": "50", "BYOK_FALLBACK_DAILY_LIMIT": "9", "FREE_DAILY_CHAT_LIMIT": "42"}
        ):
            assert _daily_limit() == 7
            assert _global_cap() == 50
            assert fallback_daily_limit() == 9
            assert so.int_param("FREE_DAILY_CHAT_LIMIT", 20) == 42
        so.invalidate_local()
        with _with_overrides({}):
            assert _daily_limit() == 3
            assert so.int_param("FREE_DAILY_CHAT_LIMIT", 20) == 20

    def test_chat_limit_call_sites_use_int_param(self):
        src = (REPO / "api" / "routers" / "analysis.py").read_text(encoding="utf-8")
        assert src.count('int_param("FREE_DAILY_CHAT_LIMIT", FREE_DAILY_CHAT_LIMIT)') == 2


class _FakeCursor:
    def __init__(self, log, fetch=None):
        self.log, self._fetch = log, list(fetch or [])

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._fetch.pop(0) if self._fetch else None


class _FakeConn:
    def __init__(self, log, fetch=None):
        self._cursor = _FakeCursor(log, fetch)

    def cursor(self):
        return self._cursor


def _fake_transaction(log, fetch=None):
    @contextmanager
    def tx():
        yield _FakeConn(log, fetch)

    return tx


class TestWrite:
    def test_set_upserts_audits_and_publishes(self):
        log = []
        with (
            patch("core.database.base.transaction", _fake_transaction(log, fetch=[("true",)])),
            patch.object(so, "publish") as publish,
        ):
            assert so.set_override("VISION_ENABLED", "OFF", "admin1") == "false"
        sqls = [s for s, _ in log]
        assert any(s.startswith("INSERT INTO admin_setting_overrides") and "ON CONFLICT (key)" in s for s in sqls)
        audit = next(p for s, p in log if s.startswith("INSERT INTO config_audit_log"))
        assert audit == ("override:VISION_ENABLED", "true", "false", "admin1")
        publish.assert_called_once()

    def test_set_rejects_invalid_before_touching_db(self):
        with patch("core.database.base.transaction") as tx:
            with pytest.raises(so.InvalidOverride):
                so.set_override("FREE_DAILY_CHAT_LIMIT", "-5", "admin1")
        tx.assert_not_called()

    def test_clear_deletes_audits_and_publishes(self):
        log = []
        with (
            patch("core.database.base.transaction", _fake_transaction(log, fetch=[("30",)])),
            patch.object(so, "publish") as publish,
        ):
            assert so.clear_override("FREE_DAILY_CHAT_LIMIT", "admin1") is True
        assert log[0][0].startswith("DELETE FROM admin_setting_overrides")
        audit = next(p for s, p in log if s.startswith("INSERT INTO config_audit_log"))
        assert audit == ("override:FREE_DAILY_CHAT_LIMIT", "30", None, "admin1")
        publish.assert_called_once()

    def test_publish_writes_whole_dict(self):
        with (
            patch.object(so, "load_rows", return_value={"VISION_ENABLED": {"value": "false"}}),
            patch("core.shared_cache.set_json") as set_json,
        ):
            assert so.publish() == {"VISION_ENABLED": "false"}
        key, data = set_json.call_args.args[:2]
        assert key == so.REDIS_KEY and data == {"VISION_ENABLED": "false"}
        assert so.get("VISION_ENABLED") == "false", "本 process 立刻看到新值"

    def test_load_rows_skips_unknown_keys(self):
        rows = [
            {"key": "VISION_ENABLED", "value": "false", "updated_by": "a", "updated_at": None},
            {"key": "PII_SCRUB_ENABLED", "value": "false", "updated_by": "a", "updated_at": None},
        ]
        with patch("core.database.base.DatabaseBase.query_all", return_value=rows):
            assert set(so.load_rows()) == {"VISION_ENABLED"}


def _client():
    from slowapi.errors import RateLimitExceeded

    from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
    from api.routers.admin import settings_center as mod

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(mod.router, prefix="/api/admin")

    async def fake_admin():
        return {"user_id": "admin1", "role": "admin"}

    app.dependency_overrides[mod.require_admin] = fake_admin
    return TestClient(app)


class TestEndpoints:
    def test_put_valid(self):
        with patch.object(so, "set_override", return_value="false") as set_override:
            res = _client().put("/api/admin/settings-center/overrides/VISION_ENABLED", json={"value": "false"})
        assert res.status_code == 200 and res.json()["value"] == "false"
        set_override.assert_called_once_with("VISION_ENABLED", "false", "admin1")

    def test_put_not_overridable_is_404(self):
        with patch.object(so, "set_override") as set_override:
            res = _client().put("/api/admin/settings-center/overrides/PII_SCRUB_ENABLED", json={"value": "false"})
        assert res.status_code == 404
        set_override.assert_not_called()

    def test_put_invalid_is_422(self):
        with patch.object(so, "set_override", side_effect=so.InvalidOverride("bad")):
            res = _client().put("/api/admin/settings-center/overrides/FREE_DAILY_CHAT_LIMIT", json={"value": "x"})
        assert res.status_code == 422

    def test_delete(self):
        with patch.object(so, "clear_override", return_value=True) as clear:
            res = _client().delete("/api/admin/settings-center/overrides/FREE_DAILY_CHAT_LIMIT")
        assert res.status_code == 200 and res.json()["removed"] is True
        clear.assert_called_once_with("FREE_DAILY_CHAT_LIMIT", "admin1")

    def test_endpoints_require_admin(self):
        from api.routers.admin import settings_center as mod

        for fn in (mod.admin_set_override, mod.admin_clear_override, mod.admin_settings_center):
            dep = inspect.signature(fn).parameters["admin_user"].default
            assert getattr(dep, "dependency", None) is mod.require_admin, fn.__name__


class TestSettingsCenterRows:
    def test_override_fields_and_services_suppressed(self):
        from core import admin_settings, feature_flags
        from core.feature_flags import FLAG_REGISTRY

        values = {name: False for name in FLAG_REGISTRY} | {"VISION_ENABLED": False}
        snaps = {
            "api": {"flags": {"VISION_ENABLED": True}, "at": "x"},
            "analysis-worker": {"flags": {"VISION_ENABLED": False}, "at": "x"},
        }
        overrides = {
            "VISION_ENABLED": {"value": "false", "updated_by": "admin1", "updated_at": "2026-09-27T00:00:00+00:00"},
            "FREE_DAILY_CHAT_LIMIT": {"value": "30", "updated_by": "admin1", "updated_at": None},
        }
        with (
            patch.object(feature_flags, "all_flags", return_value=values),
            patch.object(feature_flags, "read_service_snapshots", return_value=snaps),
            patch("core.email_brief.provider.missing_config", return_value=[]),
            patch.object(so, "load_rows", return_value=overrides),
        ):
            data = admin_settings.build_settings_center()
        vision = next(r for r in data["flags"] if r["name"] == "VISION_ENABLED")
        assert vision["overridable"] and vision["override"] == "false" and vision["override_by"] == "admin1"
        assert vision["consistent"] is True and vision["services"] == {}, "有覆寫時所有服務讀同一份，不比快照"
        pii = next(r for r in data["flags"] if r["name"] == "PII_SCRUB_ENABLED")
        assert pii["overridable"] is False and pii["override"] is None
        chat = next(r for r in data["params"] if r["name"] == "FREE_DAILY_CHAT_LIMIT")
        assert chat["overridable"] and chat["override"] == "30" and chat["bounds"] == [0, 10_000]
        price = next(r for r in data["params"] if r["name"] == "PREMIUM_MONTHLY_USD")
        assert price["overridable"] is False, "價格屬金流，不開放後台改"


class TestSchemaAndStartup:
    MIG = REPO / "alembic" / "versions" / "c055_admin_setting_overrides.py"

    @staticmethod
    def _ddl(src: str) -> str:
        m = re.search(
            r"CREATE TABLE IF NOT EXISTS admin_setting_overrides \((.*?)\n\s*\)\s*\"\"\"",
            src,
            re.S,
        )
        assert m
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_migration_chain(self):
        src = self.MIG.read_text(encoding="utf-8")
        assert 'revision = "c055"' in src and 'down_revision = "c054"' in src
        assert "DROP TABLE IF EXISTS admin_setting_overrides" in src
        downs = [
            m.group(1)
            for f in (REPO / "alembic" / "versions").glob("c05*.py")
            if (m := re.search(r'^down_revision = "(\w+)"', f.read_text(encoding="utf-8"), re.M))
        ]
        assert downs.count("c054") == 1, "c054 之後只能有一個 migration"

    def test_schema_ddl_matches_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("admin_setting_overrides", create_admin_setting_overrides_table)' in schema
        assert self._ddl(schema) == self._ddl(self.MIG.read_text(encoding="utf-8"))

    def test_api_republishes_periodically(self):
        src = (REPO / "api" / "lifespan.py").read_text(encoding="utf-8")
        assert "asyncio.create_task(republish_task())" in src
