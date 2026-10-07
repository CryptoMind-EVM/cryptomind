"""後台設定中心（唯讀）：core/admin_settings.py＋GET /api/admin/settings-center（2026-09-27）。"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from core import admin_settings, feature_flags
from core.feature_flags import FLAG_GROUPS, FLAG_REGISTRY, FLAG_REQUIREMENTS

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]


class TestRegistryMetadata:
    def test_every_flag_in_exactly_one_group(self):
        grouped = [name for names in FLAG_GROUPS.values() for name in names]
        assert sorted(grouped) == sorted(FLAG_REGISTRY), "新旗標要放進 FLAG_GROUPS（設定中心才看得到）"
        assert len(grouped) == len(set(grouped))

    def test_requirements_point_to_real_things(self):
        for name, reqs in FLAG_REQUIREMENTS.items():
            assert name in FLAG_REGISTRY
            for kind, target in reqs:
                assert kind in ("flag", "env", "email_config")
                if kind == "flag":
                    assert target in FLAG_REGISTRY, f"{name} 依賴的 {target} 沒登記"
                if kind == "env":
                    assert target and target.isupper()


class TestUnmet:
    def test_flag_and_env_requirements(self, monkeypatch):
        monkeypatch.delenv("ETHERSCAN_SERVICE_API_KEY", raising=False)
        unmet = admin_settings._unmet(
            "WALLET_MONITOR_EVM_ENABLED", {"WALLET_MONITOR_ENABLED": False}
        )
        assert unmet == ["要先開 WALLET_MONITOR_ENABLED", "缺環境變數 ETHERSCAN_SERVICE_API_KEY"]
        monkeypatch.setenv("ETHERSCAN_SERVICE_API_KEY", "k")
        assert admin_settings._unmet(
            "WALLET_MONITOR_EVM_ENABLED", {"WALLET_MONITOR_ENABLED": True}
        ) == []

    def test_email_config_lists_missing_env(self):
        with patch(
            "core.email_brief.provider.missing_config",
            return_value=["RESEND_API_KEY", "EMAIL_FROM"],
        ):
            assert admin_settings._unmet("EMAIL_BRIEF_ENABLED", {}) == [
                "缺環境變數 RESEND_API_KEY",
                "缺環境變數 EMAIL_FROM",
            ]

    def test_flag_without_requirements(self):
        assert admin_settings._unmet("VISION_ENABLED", {}) == []


class TestBuild:
    def _build(self, values, snapshots=None, missing=(), overrides=None):
        with (
            patch.object(feature_flags, "all_flags", return_value=values),
            patch.object(feature_flags, "read_service_snapshots", return_value=snapshots or {}),
            patch("core.email_brief.provider.missing_config", return_value=list(missing)),
            patch("core.setting_overrides.load_rows", return_value=overrides or {}),
        ):
            return admin_settings.build_settings_center()

    def test_every_flag_has_a_row_in_group_order(self):
        values = {name: False for name in FLAG_REGISTRY}
        data = self._build(values)
        assert [r["name"] for r in data["flags"]] == [
            n for names in FLAG_GROUPS.values() for n in names
        ]

    def test_on_but_missing_config_is_not_effective(self):
        values = {name: False for name in FLAG_REGISTRY} | {"EMAIL_BRIEF_ENABLED": True}
        row = next(
            r for r in self._build(values, missing=["RESEND_API_KEY"])["flags"]
            if r["name"] == "EMAIL_BRIEF_ENABLED"
        )
        assert row["value"] is True and row["effective"] is False
        assert row["unmet"] == ["缺環境變數 RESEND_API_KEY"]

    def test_off_flag_does_not_list_unmet(self):
        values = {name: False for name in FLAG_REGISTRY}
        row = next(
            r for r in self._build(values, missing=["RESEND_API_KEY"])["flags"]
            if r["name"] == "EMAIL_BRIEF_ENABLED"
        )
        assert row["effective"] is False and row["unmet"] == []

    def test_service_divergence_is_flagged(self):
        values = {name: False for name in FLAG_REGISTRY} | {"VISION_ENABLED": True}
        snaps = {
            "api": {"flags": {"VISION_ENABLED": True}, "at": "2026-09-27T00:00:00+00:00"},
            "analysis-worker": {"flags": {"VISION_ENABLED": False}, "at": "2026-09-27T00:00:00+00:00"},
        }
        data = self._build(values, snaps)
        row = next(r for r in data["flags"] if r["name"] == "VISION_ENABLED")
        assert row["consistent"] is False
        assert row["services"] == {"api": True, "analysis-worker": False}
        assert set(data["services"]) == {"api", "analysis-worker"}

    def test_params_read_real_values(self):
        data = self._build({name: False for name in FLAG_REGISTRY})
        by_name = {r["name"]: r for r in data["params"]}
        from core.config import (
            FORUM_TIP_MAX_USD,
            FREE_DAILY_CHAT_LIMIT,
            PREMIUM_USD_PRICES,
        )

        assert by_name["FREE_DAILY_CHAT_LIMIT"]["value"] == FREE_DAILY_CHAT_LIMIT
        assert by_name["PREMIUM_MONTHLY_USD"]["value"] == PREMIUM_USD_PRICES["premium_monthly"]
        assert by_name["FORUM_TIP_MAX_USD"]["value"] == FORUM_TIP_MAX_USD
        assert all(r["value"] is not None for r in data["params"]), "每個 getter 都要讀得到值"


class TestSnapshots:
    def test_store_and_read_roundtrip(self):
        store = {}
        with (
            patch("core.shared_cache.set_json", side_effect=lambda k, v, ttl=None: store.__setitem__(k, v)),
            patch("core.shared_cache.get_json", side_effect=lambda k: store.get(k)),
            patch.object(feature_flags, "all_flags", return_value={"VISION_ENABLED": True}),
        ):
            feature_flags.store_service_snapshot("cron-worker")
            snaps = feature_flags.read_service_snapshots()
        assert snaps["cron-worker"]["flags"] == {"VISION_ENABLED": True}
        assert "at" in snaps["cron-worker"]

    def test_startup_and_cron_report_snapshots(self):
        assert "store_service_snapshot(service)" in inspect.getsource(feature_flags.log_flag_state)
        cron = (REPO / "scripts" / "cron_daily_brief.py").read_text(encoding="utf-8")
        assert 'store_service_snapshot("cron-worker")' in cron


class TestEndpointAndConfigCleanup:
    def test_settings_center_requires_admin(self):
        from api.routers.admin import settings_center as mod

        dep = inspect.signature(mod.admin_settings_center).parameters["admin_user"].default
        assert getattr(dep, "dependency", None) is mod.require_admin
        from fastapi import FastAPI

        from api.routers.admin import router

        app = FastAPI()
        app.include_router(router)
        assert "/api/admin/settings-center" in app.openapi()["paths"]

    async def test_unused_configs_are_hidden_and_not_editable(self):
        from fastapi import HTTPException

        from api.routers.admin import config as mod
        from api.routers.admin.schemas import UpdateConfigRequest

        rows = [
            {"key": "price_premium", "category": "pricing"},
            {"key": "limit_daily_post_free", "category": "limits"},
            {"key": "scam_list_page_size", "category": "scam_tracker"},
        ]
        with patch.object(mod, "list_all_configs_with_metadata", return_value=rows):
            out = await mod.admin_get_all_configs(admin_user={"user_id": "a"})
        keys = [c["key"] for cats in out["configs_by_category"].values() for c in cats]
        assert keys == ["limit_daily_post_free"]

        with patch.object(mod, "set_config") as set_config:
            with pytest.raises(HTTPException) as exc:
                await mod.admin_update_config.__wrapped__(
                    "price_premium",
                    request=None,
                    body=UpdateConfigRequest(value="1"),
                    admin_user={"user_id": "a"},
                )
        assert exc.value.status_code == 404
        set_config.assert_not_called()


class TestFrontendWiring:
    def test_module_nav_and_allowlist(self):
        spa = (REPO / "web/js/spa.js").read_text(encoding="utf-8")
        assert "import('./admin-settings.js')" in spa
        admin = (REPO / "web/js/admin.js").read_text(encoding="utf-8")
        assert 'id="admin-subnav-settings"' in admin
        assert "AdminSettingsCenter.render()" in admin
        delegator = (REPO / "web/js/click-delegator.js").read_text(encoding="utf-8")
        assert "'AdminSettingsCenter'" in delegator

    @pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node 不可用")
    def test_node_render(self):
        import subprocess

        out = subprocess.run(
            ["node", "tests/js/admin_settings.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
