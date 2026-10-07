"""全面盤查（2026-09-25）後端批：錯誤不外洩、query_one 拒收寫入、cron 單一實例與失敗離場碼。"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


class TestDbErrorsNotLeaked:
    """DB 例外（表名、欄位、constraint 名）只進 log，回給路由的是固定代碼。"""

    def _boom_conn(self):
        class Cur:
            def execute(self, *a, **k):
                raise RuntimeError('relation "dm_messages" violates constraint dm_pk')

            def fetchone(self):
                return None

            def close(self):
                pass

        class Conn:
            def cursor(self):
                return Cur()

            def rollback(self):
                pass

            def commit(self):
                pass

            def close(self):
                pass

        return Conn()

    def test_message_helpers(self):
        from core.database.messages import helpers

        with patch.object(helpers, "get_connection", return_value=self._boom_conn()):
            for res in (
                helpers.hide_dm_message_for_user(1, "u1"),
                helpers.hide_conversation_for_user(1, "u1"),
            ):
                assert res == {"success": False, "error": "internal_error"}

    def test_friends(self):
        from core.database import friends

        with patch.object(friends, "get_connection", return_value=self._boom_conn()):
            res = friends.send_friend_request("u1", "u2")
        assert res["success"] is False and "dm_messages" not in str(res)


class TestQueryHelpersRejectWrites:
    """query_one／query_all 不 commit：寫入丟進去會「成功」但被 rollback（2026-09-12 兩次事故）。"""

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO t (a) VALUES (%s) RETURNING id",
            "update users set role = 'admin' where user_id = %s",
            "DELETE FROM t WHERE id = %s",
            "  -- 註解\n  /* 區塊 */ INSERT INTO t (a) VALUES (1)",
        ],
    )
    def test_write_sql_raises_before_connecting(self, sql):
        from core.database import base

        with patch.object(base, "get_connection") as conn:
            with pytest.raises(base.DatabaseError):
                base.DatabaseBase.query_one(sql, (1,))
            with pytest.raises(base.DatabaseError):
                base.DatabaseBase.query_all(sql, (1,))
        conn.assert_not_called()

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1",
            "SELECT * FROM t WHERE id = %s FOR UPDATE SKIP LOCKED",
            # 字串值與註解裡的 DML 字眼不算寫入（review 抓到的誤判）
            "SELECT * FROM t WHERE action = 'delete from queue' AND id = %s",
            "SELECT 1 -- truncate would clear this table",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ],
    )
    def test_reads_allowed(self, sql):
        from core.database import base

        base._reject_write_sql(sql)


@pytest.mark.skipif(sys.platform == "win32", reason="core/cron_lock 用 fcntl（Linux 專用）")
class TestCronLock:
    def test_second_run_is_skipped_until_first_ends(self, tmp_path, monkeypatch):
        from core import cron_lock

        monkeypatch.setattr(cron_lock.tempfile, "gettempdir", lambda: str(tmp_path))
        first = cron_lock.acquire("job")
        assert first is not None
        assert cron_lock.acquire("job") is None, "上一輪還在跑，這一輪要跳過"
        assert cron_lock.acquire("other-job") is not None
        first.close()
        assert cron_lock.acquire("job") is not None

    def test_scripts_take_the_lock(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        for script, name in (
            ("scripts/cron_wallet_monitor.py", "wallet-monitor"),
            ("scripts/cron_daily_brief.py", "daily-brief"),
        ):
            src = (root / script).read_text(encoding="utf-8")
            assert f'cron_lock.acquire("{name}")' in src, script


class TestDailyBriefExitCode:
    @pytest.mark.parametrize(
        "summary, code",
        [
            ({"due": 3, "sent": 0, "empty": 0, "failed": 3}, 1),
            ({"due": 3, "sent": 1, "empty": 0, "failed": 2}, 0),
            ({"due": 2, "sent": 0, "empty": 2, "failed": 0}, 0),
            ({"due": 0, "sent": 0, "empty": 0, "failed": 0}, 0),
        ],
    )
    def test_all_failed_is_nonzero(self, summary, code):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "scripts" / "cron_daily_brief.py"
        spec = importlib.util.spec_from_file_location("cron_daily_brief", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.exit_code(summary) == code
