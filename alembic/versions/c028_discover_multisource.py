"""discover_multisource_p0_p2

Revision ID: c028
Revises: c027
Create Date: 2026-08-17

Discover 多來源（design 2026-08-17-discover-multisource，DANNY Approved「要就請全部做完」）：
- ``user_favorites.ck_favorite_item_type_valid`` 放寬 + ``oc_collective``
  （Phase 1：Open Collective 收藏）
- ``proposal_drafts`` 補 ``references_json``（Phase 2：Discover→Studio 參考動線）

純放寬/新增，既有列不動；Idempotent；self-healed by
``create_agent_platform_tables`` at startup。
"""

from alembic import op


revision = "c028"
down_revision = "c027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_favorites DROP CONSTRAINT IF EXISTS ck_favorite_item_type_valid"
    )
    op.execute(
        "ALTER TABLE user_favorites ADD CONSTRAINT ck_favorite_item_type_valid "
        "CHECK (item_type IN ('manifund_project','manifund_user','oc_collective'))"
    )
    op.execute(
        "ALTER TABLE proposal_drafts ADD COLUMN IF NOT EXISTS references_json "
        "JSONB DEFAULT '[]'"
    )


def downgrade() -> None:
    # 收緊前須先清掉 oc_collective 收藏列，否則 CHECK 會擋——保守起見直接刪
    # （收藏是輕量標記，可重加；reference 同理屬可重建資料）。
    op.execute("DELETE FROM user_favorites WHERE item_type = 'oc_collective'")
    op.execute(
        "ALTER TABLE user_favorites DROP CONSTRAINT IF EXISTS ck_favorite_item_type_valid"
    )
    op.execute(
        "ALTER TABLE user_favorites ADD CONSTRAINT ck_favorite_item_type_valid "
        "CHECK (item_type IN ('manifund_project','manifund_user'))"
    )
    op.execute("ALTER TABLE proposal_drafts DROP COLUMN IF EXISTS references_json")
