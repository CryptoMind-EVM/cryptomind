"""unique_display_name

Revision ID: c011
Revises: c010
Create Date: 2026-07-24

Add a UNIQUE constraint on ``users.display_name`` so two users cannot hold the
same personalized nickname (forum/community scenario — duplicate names cause
confusion).

Background: ``display_name`` was added in c010 as a plain nullable TEXT with no
uniqueness. The rename endpoint (``PUT /api/user/display-name``) also did not
check for collisions. This migration:

1. **Dedupes existing rows** — for every ``display_name`` value held by more
   than one user, keeps only the single earliest-created row and sets the rest
   to NULL. (Decision: affected users can simply pick the name again next
   time; the DB constraint is what matters going forward.)
2. **Creates the unique index** — ``CREATE UNIQUE INDEX`` on ``display_name``.
   Postgres allows multiple NULLs on a nullable unique column, so users who
   never set a nickname are unaffected.

The constraint is also enforced at:
- ``core/database/schema.py`` CREATE TABLE (``display_name TEXT UNIQUE``) for
  freshly-init'd DBs, and ``reconcile_user_tables`` for self-healing.
- ``core/orm/models.py`` ``unique=True`` for Alembic autogenerate consistency.
- ``PUT /api/user/display-name`` pre-check + ``set_user_display_name``
  UniqueViolation catch for clear 409/422 responses.

⚠️ 不可逆資料變更:step 1 會把重複暱稱的使用者 display_name 清為 NULL。
已獲產品確認(重複者清為 NULL)。

Note: ``ALTER TABLE ... ADD CONSTRAINT`` would fail if duplicates exist, so we
dedupe first then use ``CREATE UNIQUE INDEX IF NOT EXISTS`` (idempotent, matches
project style). A plain unique index and a table UNIQUE constraint enforce the
same thing; index is easier to manage/drop.
"""

from alembic import op


revision = "c011"
down_revision = "c010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 去重:每組重複 display_name 只保留最早建立的一筆(min created_at,
    #    tie-break by user_id),其餘清為 NULL。用 CTE 標記要保留的首筆。
    op.execute(
        """
        WITH dup_groups AS (
            SELECT display_name
            FROM users
            WHERE display_name IS NOT NULL
            GROUP BY display_name
            HAVING COUNT(*) > 1
        ),
        keep_one AS (
            SELECT DISTINCT ON (u.display_name) u.display_name, u.user_id
            FROM users u
            JOIN dup_groups d ON u.display_name = d.display_name
            ORDER BY u.display_name, u.created_at ASC, u.user_id ASC
        )
        UPDATE users u SET display_name = NULL
        FROM dup_groups
        WHERE u.display_name = dup_groups.display_name
          AND u.user_id NOT IN (SELECT user_id FROM keep_one)
        """
    )

    # 2. 建唯一索引(NULL 不參與唯一性比對,多個 NULL OK)
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_display_name ON users(display_name)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_users_display_name")
