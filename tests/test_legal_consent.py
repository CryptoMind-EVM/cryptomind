"""條款／隱私政策改版後的同意紀錄（2026-09-27；core/legal.py、c054）。

全部 mock：不打 DB。前端行為在 tests/js/legal_consent.mjs（node 實跑）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import legal

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
USER = {"user_id": "evm_0xabc", "username": "u", "membership_tier": "free"}


def _client():
    from slowapi.errors import RateLimitExceeded

    from api.middleware.rate_limit import limiter, rate_limit_exceeded_handler
    from api.routers import legal_consent as mod

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.include_router(mod.router)

    async def fake_user():
        return USER

    app.dependency_overrides[mod.get_current_user] = fake_user
    return TestClient(app)


class TestStore:
    def test_record_is_idempotent_insert(self):
        with patch("core.legal.DatabaseBase.execute") as execute:
            legal.record_acceptance("u1", "2026-09-27")
        sql, params = execute.call_args.args
        assert sql.startswith("INSERT INTO user_legal_acceptances")
        assert "ON CONFLICT (user_id, version) DO NOTHING" in sql
        assert params == ("u1", "2026-09-27")

    def test_has_accepted_checks_this_version(self):
        with patch("core.legal.DatabaseBase.query_one", return_value={"ok": 1}) as q:
            assert legal.has_accepted("u1") is True
        assert q.call_args.args[1] == ("u1", legal.LEGAL_VERSION)
        with patch("core.legal.DatabaseBase.query_one", return_value=None):
            assert legal.has_accepted("u1") is False


class TestAcceptEndpoint:
    def test_records_current_version_and_invalidates_me_cache(self):
        from api.routers.user import _ME_CACHE

        _ME_CACHE["evm_0xabc"] = {"stale": True}
        with patch("core.legal.record_acceptance") as record:
            res = _client().post(
                "/api/user/legal/accept", json={"version": legal.LEGAL_VERSION}
            )
        assert res.status_code == 200
        assert res.json() == {"success": True, "version": legal.LEGAL_VERSION}
        record.assert_called_once_with("evm_0xabc", legal.LEGAL_VERSION)
        assert "evm_0xabc" not in _ME_CACHE, "同意後 /me 快取要失效"

    def test_stale_version_is_409_and_not_recorded(self):
        with patch("core.legal.record_acceptance") as record:
            res = _client().post("/api/user/legal/accept", json={"version": "2020-01-01"})
        assert res.status_code == 409
        record.assert_not_called()

    def test_db_failure_is_500(self):
        with patch("core.legal.record_acceptance", side_effect=RuntimeError("db down")):
            res = _client().post(
                "/api/user/legal/accept", json={"version": legal.LEGAL_VERSION}
            )
        assert res.status_code == 500

    def test_requires_login(self):
        # 測試環境 TEST_MODE 下沒 token 也會拿到合成使用者，只能檢查有掛登入依賴
        import inspect

        from api.routers import legal_consent as mod

        dep = inspect.signature(mod.accept_legal).parameters["current_user"].default
        assert getattr(dep, "dependency", None) is mod.get_current_user


class TestMeIncludesLegal:
    async def _me(self, has_accepted):
        from api.routers import user as user_router

        user_router._ME_CACHE.pop("evm_0xabc", None)
        with (
            patch.object(user_router.user_repo, "get_language", new=AsyncMock(return_value="zh-TW")),
            patch.object(user_router.user_repo, "get_display_name", new=AsyncMock(return_value=None)),
            patch.object(
                user_router.user_llm_preferences_repo,
                "get_selected_provider",
                new=AsyncMock(return_value=None),
            ),
            patch("core.membership_reminder.maybe_notify_membership_expiring", new=AsyncMock()),
            patch("core.legal.has_accepted", has_accepted),
        ):
            res = await user_router.get_current_user_profile(current_user=dict(USER))
        user_router._ME_CACHE.pop("evm_0xabc", None)
        return res["user"]["legal"]

    async def test_not_accepted(self):
        assert await self._me(MagicMock(return_value=False)) == {
            "version": legal.LEGAL_VERSION,
            "accepted": False,
        }

    async def test_accepted(self):
        assert (await self._me(MagicMock(return_value=True)))["accepted"] is True

    async def test_lookup_failure_does_not_break_me(self):
        assert await self._me(MagicMock(side_effect=RuntimeError("db down"))) is None


class TestSchemaAndMigration:
    MIG = REPO / "alembic" / "versions" / "c054_user_legal_acceptances.py"

    @staticmethod
    def _ddl(src: str) -> str:
        m = re.search(
            r"CREATE TABLE IF NOT EXISTS user_legal_acceptances \((.*?)\n\s*\)\s*\"\"\"",
            src,
            re.S,
        )
        assert m, "找不到 user_legal_acceptances DDL"
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_migration_chain_and_downgrade(self):
        src = self.MIG.read_text(encoding="utf-8")
        assert 'revision = "c054"' in src and 'down_revision = "c053"' in src
        assert "DROP TABLE IF EXISTS user_legal_acceptances" in src
        assert "ON DELETE CASCADE" in src

    def test_single_head_after_c053(self):
        downs = []
        for f in (REPO / "alembic" / "versions").glob("c05*.py"):
            m = re.search(r'^down_revision = "(\w+)"', f.read_text(encoding="utf-8"), re.M)
            if m:
                downs.append(m.group(1))
        assert downs.count("c053") == 1, "c053 之後只能有一個 migration（兩個 head 會卡部署）"

    def test_schema_ddl_matches_migration(self):
        schema = (REPO / "core" / "database" / "schema.py").read_text(encoding="utf-8")
        assert '("user_legal_acceptances", create_user_legal_acceptances_table)' in schema
        assert self._ddl(schema) == self._ddl(self.MIG.read_text(encoding="utf-8"))


class TestLegalPagesMatchVersion:
    """改了條款卻忘了升 LEGAL_VERSION（或反過來）→ 使用者不會被要求重新同意。"""

    @pytest.mark.parametrize("page", ["terms-of-service.html", "privacy-policy.html"])
    def test_last_updated_date_is_legal_version(self, page):
        src = (REPO / "web" / "legal" / page).read_text(encoding="utf-8")
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日（<span data-zh=\"最後更新\"", src)
        assert m, f"{page} 找不到「最後更新」日期"
        y, mo, d = (int(x) for x in m.groups())
        assert f"{y:04d}-{mo:02d}-{d:02d}" == legal.LEGAL_VERSION

    def test_privacy_policy_discloses_consent_records(self):
        src = (REPO / "web" / "legal" / "privacy-policy.html").read_text(encoding="utf-8")
        assert src.count('data-zh="條款同意紀錄"') == 2, "§1.2 蒐集項目＋§6 保留期限"


class TestFrontendWiring:
    def test_card_module_and_allowlist(self):
        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        assert 'id="legal-consent-card"' in html
        assert 'data-click="LegalConsent.accept"' in html
        assert "import './legal-consent.js'" in (REPO / "web/js/main.js").read_text(
            encoding="utf-8"
        )
        assert "'LegalConsent'" in (REPO / "web/js/click-delegator.js").read_text(
            encoding="utf-8"
        )
        assert "legal: backendUser.legal" in (REPO / "web/js/auth.js").read_text(
            encoding="utf-8"
        )

    def test_card_sits_at_bottom_above_input_bar(self):
        """DANNY 2026-10-01：「隱私服務政策也應該顯示在底部，怎麼會跑到頂部」。手機以前放上方（蓋住標題列）；
        改貼底、底邊跟 toast 同一條基準線（--shell-toast-bottom，ui-shell 算好輸入列的高度），不蓋輸入框"""
        import re

        html = (REPO / "web" / "index.html").read_text(encoding="utf-8")
        tag = re.search(r'<div id="legal-consent-card"[^>]*>', html).group(0)
        assert not re.search(r"(?<![\w:-])top-\d", tag), "手機不能再釘在頂部"
        css = (REPO / "web" / "styles.css").read_text(encoding="utf-8")
        rule = css[css.index("#legal-consent-card {") :][:200]
        assert "bottom: var(--shell-toast-bottom" in rule, "貼底、在輸入列之上"

    @pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
    def test_node_behaviour(self):
        out = subprocess.run(
            ["node", "tests/js/legal_consent.mjs"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert out.returncode == 0, (out.stderr or out.stdout)[-3000:]
