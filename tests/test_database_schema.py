"""Tests for schema create/reconcile helpers."""

from unittest.mock import MagicMock

from core.database.schema import (
    format_reconcile_summary,
    reconcile_existing_tables,
    reconcile_payment_tables,
)


class TestSchemaReconcile:
    """Tests for safe schema reconciliation."""

    def test_reconcile_existing_tables_runs_key_table_steps(self):
        """Reconcile should check the legacy tables we support at startup."""
        mock_cursor = MagicMock()

        summary = reconcile_existing_tables(mock_cursor)

        assert "users" in summary
        assert "user_feedback" in summary
        assert "audit_logs" in summary
        # 2026-10-01 起刪欄位（scam_report_comments.attachment_url）不在啟動路徑
        assert "scam_report_comments" not in summary
        assert "tools_catalog" in summary
        assert "payment_tables" in summary
        assert any(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role" in call.args[0]
            for call in mock_cursor.execute.call_args_list
        )
        assert any(
            'ALTER TABLE "user-massege" RENAME TO user_feedback' in call.args[0]
            for call in mock_cursor.execute.call_args_list
        )
        assert any(
            "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS endpoint" in call.args[0]
            for call in mock_cursor.execute.call_args_list
        )
        assert not any(
            "ALTER TABLE scam_report_comments DROP COLUMN IF EXISTS attachment_url"
            in call.args[0]
            for call in mock_cursor.execute.call_args_list
        ), "刪欄位不在啟動路徑（2026-10-01）"
        assert any(
            "UPDATE audit_logs SET endpoint = COALESCE(endpoint, 'system://legacy')"
            in call.args[0]
            for call in mock_cursor.execute.call_args_list
        )

    def test_reconcile_existing_tables_adds_tx_hash_unique(self):
        """Reconcile should add UNIQUE constraints on tx_hash columns."""
        mock_cursor = MagicMock()

        reconcile_existing_tables(mock_cursor)

        sql_calls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any(
            "idx_membership_payments_tx_hash" in sql and "UNIQUE" in sql
            for sql in sql_calls
        )
        assert any("idx_tips_tx_hash" in sql and "UNIQUE" in sql for sql in sql_calls)
        assert any(
            "idx_posts_payment_tx_hash" in sql and "UNIQUE" in sql for sql in sql_calls
        )
        assert any("payment_tx_hash IS NOT NULL" in sql for sql in sql_calls)

    def test_reconcile_payment_tables_success(self):
        """reconcile_payment_tables returns checked items on success."""
        mock_cursor = MagicMock()

        result = reconcile_payment_tables(mock_cursor)

        assert "membership_payments.tx_hash_unique" in result
        assert "tips.tx_hash_unique" in result
        assert "posts.payment_tx_hash_unique_partial" in result
        # c041: 多鏈付款欄位（chain / asset / fiat_amount_usd）
        assert "membership_payments.multichain_columns" in result
        # 1 unique index + 3 ALTER ADD COLUMN + tips + posts
        assert mock_cursor.execute.call_count == 6

    def test_reconcile_payment_tables_handles_failure_gracefully(self):
        """reconcile_payment_tables should continue even if one statement fails."""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = [
            Exception("duplicate key"),
            None,
            None,
            None,
            None,
            None,
        ]

        result = reconcile_payment_tables(mock_cursor)

        assert "membership_payments.tx_hash_unique" not in result
        assert "membership_payments.multichain_columns" in result
        assert "tips.tx_hash_unique" in result
        assert "posts.payment_tx_hash_unique_partial" in result
        assert mock_cursor.execute.call_count == 6

    def test_format_reconcile_summary_compacts_counts(self):
        """Startup logging should format reconcile counts in a compact form."""
        summary = {
            "users": ["role", "is_active"],
            "audit_logs": ["endpoint"],
            "tools_catalog": [],
            "payment_tables": ["membership_payments.tx_hash_unique"],
        }

        assert (
            format_reconcile_summary(summary)
            == "users(2), audit_logs(1), payment_tables(1)"
        )


class TestSchemaDDL:
    """Tests for DDL statements in CREATE TABLE definitions."""

    def test_membership_payments_tx_hash_has_unique(self):
        """membership_payments CREATE TABLE should include UNIQUE on tx_hash."""
        from core.database.schema import create_user_tables

        mock_cursor = MagicMock()
        create_user_tables(mock_cursor)

        sql_calls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        ddl = [
            s for s in sql_calls if "membership_payments" in s and "CREATE TABLE" in s
        ]
        assert len(ddl) == 1
        assert "tx_hash" in ddl[0]
        assert "UNIQUE" in ddl[0]

    def test_tips_tx_hash_has_unique(self):
        """tips CREATE TABLE should include UNIQUE on tx_hash."""
        from core.database.schema import create_forum_tables

        mock_cursor = MagicMock()
        create_forum_tables(mock_cursor)

        sql_calls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        ddl = [s for s in sql_calls if "CREATE TABLE IF NOT EXISTS tips" in s]
        assert len(ddl) == 1
        assert "tx_hash" in ddl[0]
        assert "UNIQUE" in ddl[0]

    def test_posts_payment_tx_hash_partial_unique_index(self):
        """posts should have a partial unique index on payment_tx_hash."""
        from core.database.schema import create_indexes

        mock_cursor = MagicMock()
        create_indexes(mock_cursor)

        sql_calls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any(
            "idx_posts_payment_tx_hash" in sql
            and "UNIQUE" in sql
            and "payment_tx_hash IS NOT NULL" in sql
            for sql in sql_calls
        )

    def test_user_facts_reconcile_runs_before_category_index(self):
        """category 欄位補欄位必須在 user_category 索引之前。

        迴歸：舊版 user_facts 表沒有 category 欄位時，CREATE INDEX ...
        ON user_facts(user_id, category) 會因欄位不存在而炸掉整個 step。
        正確順序是先 reconcile_user_facts_columns 補欄位、再建索引。
        """
        from core.database.schema import create_user_facts_table

        mock_cursor = MagicMock()
        create_user_facts_table(mock_cursor)

        sql_calls = [call.args[0] for call in mock_cursor.execute.call_args_list]
        # 找出「補 category 欄位」與「建 category 索引」兩個語句的位置
        alter_idx = next(
            (
                i
                for i, s in enumerate(sql_calls)
                if "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS" in s
                and "category" in s
            ),
            None,
        )
        index_idx = next(
            (
                i
                for i, s in enumerate(sql_calls)
                if "idx_user_facts_user_category" in s
            ),
            None,
        )
        assert alter_idx is not None, "缺少補 category 欄位的 ALTER"
        assert index_idx is not None, "缺少 category 索引"
        assert alter_idx < index_idx, "補欄位必須在索引之前（原本的順序 bug）"


def test_failed_reconcile_step_does_not_abort_the_rest(monkeypatch):
    """真 PostgreSQL：一步失敗只退回那一步，後面照樣生效、交易沒有進 aborted。

    2026-10-01 前沒有 savepoint：reconcile_check_constraints 撞 DuplicateObject 後交易中止，
    後面每步靜靜失敗、init_db 最後的 commit 變成 rollback——已初始化的 DB 上整批 reconcile 白做。
    """
    import os

    import psycopg2
    import pytest

    from core.database import schema

    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=3)
    except Exception:  # noqa: BLE001
        pytest.skip("沒有可用的 PostgreSQL")
    c = conn.cursor()
    try:
        c.execute("CREATE TEMP TABLE reconcile_probe (id INT)")

        def add_probe_column(cur):
            cur.execute("ALTER TABLE reconcile_probe ADD COLUMN IF NOT EXISTS added INT")
            return ["added"]

        monkeypatch.setattr(
            schema,
            "SAFE_RECONCILE_STEPS",
            (
                ("boom", lambda cur: cur.execute("ALTER TABLE no_such_table_x ADD COLUMN a INT")),
                ("probe", add_probe_column),
            ),
        )
        summary = schema.reconcile_existing_tables(c)

        assert summary == {"boom": [], "probe": ["added"]}
        c.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'reconcile_probe' AND column_name = 'added'"
        )
        assert c.fetchone(), "失敗那步之後的步驟要真的生效"
    finally:
        conn.rollback()
        conn.close()
