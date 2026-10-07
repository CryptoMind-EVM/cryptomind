"""Tests for ORM migration and startup integration.

The legacy ``core.orm.auto_migrate`` module has been replaced by Alembic.
These tests now verify that the old module is gone and that the new
Alembic-based migration is wired into the startup lifecycle.
"""

import pytest


def _read_file(path: str) -> str:
    """Read a file and return its contents."""
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestAutoMigrateModule:
    """Verify the old auto_migrate module has been replaced by Alembic."""

    def test_module_does_not_exist(self):
        """core.orm.auto_migrate should no longer be importable."""
        with pytest.raises(ImportError):
            import core.orm.auto_migrate  # noqa: F401

    def test_alembic_config_exists(self):
        """alembic.ini should exist at the project root."""
        import os

        assert os.path.isfile("alembic.ini")

    def test_alembic_env_file_exists(self):
        """alembic/env.py should exist."""
        import os

        assert os.path.isfile("alembic/env.py")


class TestAutoMigrateSafety:
    """Verify Alembic migration is properly configured instead of raw SQL auto_migrate."""

    def test_alembic_upgrade_head_in_lifespan(self):
        """The lifespan function should call alembic upgrade to head."""
        src = _read_file("api/lifespan.py")
        assert "alembic" in src.lower()
        assert "upgrade" in src.lower()

    def test_no_drop_table_in_lifespan(self):
        """The lifespan function should not contain raw DROP TABLE statements."""
        src = _read_file("api/lifespan.py")
        assert "DROP TABLE" not in src.upper()

    def test_no_alter_column_in_lifespan(self):
        """The lifespan function should not contain raw ALTER TABLE statements for columns."""
        src = _read_file("api/lifespan.py")
        assert "ALTER TABLE" not in src.upper()


class TestStartupIntegration:
    """Verify Alembic migration is wired into the startup lifecycle."""

    def test_startup_calls_alembic_upgrade(self):
        src = _read_file("api/lifespan.py")
        assert "alembic" in src.lower()
        assert "upgrade" in src.lower()
        assert "head" in src.lower()

    def test_alembic_after_db_init(self):
        src = _read_file("api/lifespan.py")
        pos_db = src.index("_init_database_background")
        pos_orm = src.index("upgrade")
        assert pos_orm > pos_db, "alembic upgrade should run after DB init"

    def test_shutdown_closes_async_engine(self):
        src = _read_file("api/lifespan.py")
        assert "close_async_engine" in src

    def test_shutdown_logs_on_error(self):
        src = _read_file("api/lifespan.py")
        # Shutdown error handling should use logger.error, not bare except
        assert "logger.error" in src

    def test_lifespan_tracks_db_ready_state(self):
        src = _read_file("api/lifespan.py")
        assert "reset_db_ready_state" in src
        assert "mark_db_ready" in src
        assert "mark_db_failed" in src

    def test_forum_app_does_not_reinitialize_auth(self):
        src = _read_file("web/js/forum-app.js")
        assert "AuthManager.init();" not in src

    def test_forum_pages_load_api_client_before_auth(self):
        # 2026-08-24 chunk 隔離重構（PR #551）：各論壇頁改為單一薄 entry
        # web/js/pages/forum-*.js，載入順序契約移入 boot 模組的 import 順序
        # （ES module 循序執行 side-effect：api-client 必須在 auth 之前）。
        boot_map = {
            "web/forum/dashboard.html": "forum-dashboard",
            "web/forum/post.html": "forum-post",
            "web/forum/create.html": "forum-create",
            "web/forum/profile.html": "forum-profile",
            "web/forum/premium.html": "forum-premium",
        }
        for page, boot_name in boot_map.items():
            html = _read_file(page)
            assert f'src="/js/pages/{boot_name}.js"' in html, (
                f"{page} 應載入薄 entry {boot_name}.js"
            )
            src = _read_file(f"web/js/pages/{boot_name}.js")
            has_auth = "'../auth.js'" in src
            assert "'../api-client.js'" in src or not has_auth, (
                f"{boot_name}.js 未載入 api-client"
            )
            if has_auth:
                assert src.index("'../api-client.js'") < src.index("'../auth.js'"), (
                    f"{boot_name}.js 的 api-client 必須在 auth 之前（auth 依賴 AppAPI）"
                )
