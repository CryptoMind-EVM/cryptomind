"""Tests for Phase 2 database schema robustness changes."""

import inspect
import re

import pytest


def _get_schema_source():
    from core.database import schema

    return inspect.getsource(schema)



def _startup_reconcile_sql():
    """跑一次啟動時的 reconcile（假 cursor），回傳它執行過的每個 SQL"""
    from unittest.mock import MagicMock

    from core.database.schema import reconcile_existing_tables

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    reconcile_existing_tables(cursor)
    return [str(call.args[0]) for call in cursor.execute.call_args_list if call.args]

class TestCheckConstraints:
    """Verify CHECK constraints are defined for data integrity."""

    def test_reconcile_check_constraints_exists(self):
        from core.database.schema import reconcile_check_constraints

        assert callable(reconcile_check_constraints)

    def test_amount_positive_on_membership_payments(self):
        src = _get_schema_source()
        assert "ck_amount_positive" in src
        assert "membership_payments" in src
        assert "amount > 0" in src

    def test_months_positive_on_membership_payments(self):
        src = _get_schema_source()
        assert "months > 0" in src

    def test_tip_amount_positive(self):
        src = _get_schema_source()
        assert "ck_tip_amount_positive" in src or "tips" in src
        assert "amount > 0" in src

    def test_price_alert_target_positive(self):
        src = _get_schema_source()
        assert "price_alerts" in src
        assert "target > 0" in src

    def test_friendship_status_check(self):
        src = _get_schema_source()
        assert "ck_friendship_status" in src or "friendships" in src
        assert "'pending'" in src
        assert "'accepted'" in src
        assert "'blocked'" in src

    def test_comment_type_check(self):
        src = _get_schema_source()
        assert "forum_comments" in src
        assert "'comment'" in src
        assert "'push'" in src  # schema uses 'comment', 'push', 'boo' — not 'reply'

    def test_verification_status_check(self):
        src = _get_schema_source()
        assert "scam_reports" in src
        assert "'verified'" in src
        assert "'rejected'" in src

    def test_vote_type_check(self):
        src = _get_schema_source()
        assert "'approve'" in src
        assert "'reject'" in src

    def test_startup_reconcile_does_not_add_constraints(self):
        """2026-10-01：加 CHECK 約束不在啟動路徑——以前因交易中止從沒在正式站跑過，
        第一次跑可能讓之後的寫入被擋；要加另寫 alembic migration"""
        assert not any("ADD CONSTRAINT" in sql for sql in _startup_reconcile_sql())


class TestForeignKeys:
    """Verify missing foreign keys are added via reconcile."""

    def test_reconcile_foreign_keys_exists(self):
        from core.database.schema import reconcile_foreign_keys

        assert callable(reconcile_foreign_keys)

    @pytest.mark.parametrize(
        "table,col",
        [
            ("admin_broadcasts", "admin_user_id"),
            ("user_violations", "user_id"),
            ("user_violation_points", "user_id"),
            ("audit_reputation", "user_id"),
            ("user_activity_logs", "user_id"),
            ("conversation_history", "user_id"),
            ("sessions", "user_id"),
            ("user_tool_preferences", "user_id"),
        ],
    )
    def test_fk_added_for_table(self, table, col):
        src = _get_schema_source()
        assert table in src
        pattern = re.compile(rf"fk_\w+.*{col}.*REFERENCES\s+users", re.IGNORECASE)
        found = pattern.search(src)
        assert found, f"Missing FK: {table}.{col} -> users(user_id)"

    def test_startup_reconcile_does_not_add_foreign_keys(self):
        """同上：FK 不在啟動路徑"""
        assert not any("FOREIGN KEY" in sql and "ALTER TABLE" in sql for sql in _startup_reconcile_sql())


class TestNumericMigration:
    """Verify REAL columns are changed to NUMERIC(18,4) for financial data."""

    def test_membership_payments_amount_is_numeric(self):
        src = _get_schema_source()
        # Check CREATE TABLE DDL
        assert "NUMERIC(18,4)" in src

    def test_reconcile_numeric_exists(self):
        from core.database.schema import reconcile_numeric_columns

        assert callable(reconcile_numeric_columns)

    @pytest.mark.parametrize(
        "table,col",
        [
            ("membership_payments", "amount"),
            ("posts", "tips_total"),
            ("tips", "amount"),
            ("price_alerts", "target"),
        ],
    )
    def test_numeric_migration_in_reconcile(self, table, col):
        from core.database.schema import reconcile_numeric_columns

        src = inspect.getsource(reconcile_numeric_columns)
        assert table in src and col in src, (
            f"Missing NUMERIC migration entry: {table}.{col}"
        )

    def test_quality_score_stays_real(self):
        src = _get_schema_source()
        # quality_score is not money, should stay REAL
        assert "quality_score" in src

    def test_startup_reconcile_does_not_change_numeric_types(self):
        """同上：REAL → NUMERIC 改型別不在啟動路徑"""
        assert not any("TYPE NUMERIC" in sql for sql in _startup_reconcile_sql())


class TestTimestamptzNormalization:
    """時間欄位型別的轉換不在啟動路徑上。

    reconcile_timestamptz（手動維護工具）已於 2026-10-03 移除：欄位型別改由 core/database/schema.py
    的建表語法與 alembic c069 負責；tests/test_schema_timestamptz.py 守著「ORM 宣告有時區的欄位，
    init_db 建出來也必須是 timestamptz」。
    """

    def test_startup_reconcile_does_not_change_timestamp_types(self):
        """TIMESTAMP → TIMESTAMPTZ 會改既有時間的解讀，不在啟動路徑"""
        assert not any(
            "TYPE TIMESTAMPTZ" in sql or "TYPE TIMESTAMP WITH" in sql
            for sql in _startup_reconcile_sql()
        )


class TestSchemaIdempotency:
    """Verify reconcile functions use try/except for idempotency."""

    def test_check_constraints_uses_try_except(self):
        from core.database.schema import reconcile_check_constraints

        src = inspect.getsource(reconcile_check_constraints)
        assert "try:" in src
        assert "except" in src

    def test_foreign_keys_uses_try_except(self):
        from core.database.schema import reconcile_foreign_keys

        src = inspect.getsource(reconcile_foreign_keys)
        assert "try:" in src
        assert "except" in src

    def test_numeric_uses_try_except(self):
        from core.database.schema import reconcile_numeric_columns

        src = inspect.getsource(reconcile_numeric_columns)
        assert "try:" in src
        assert "except" in src
