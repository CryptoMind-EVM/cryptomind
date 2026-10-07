"""解除封鎖恢復好友、互相封鎖不再互相抹掉：friendships 兩個欄位（c062）

2026-09-29 DANNY 選 LINE 式：以前封鎖會刪掉好友關係、解除封鎖不會補回，兩個原本的好友
解除封鎖後就不能再私訊。封鎖列記 restore_on_unblock（封鎖前是好友），最後一個人解除時恢復。
一對人只能有一列（idx_friendships_ordered_pair），以前 B 封鎖 A 會把 A 對 B 的封鎖整列換掉，
B 之後解除，A 就又收得到 B 的私訊；現在記在 mutual_block（friend_id 也封鎖了 user_id）。
既有的封鎖列沒有這兩份紀錄，預設 false、維持原樣。
Rollback：``alembic downgrade c061``（拿掉兩個欄位，回到舊行為）。
"""

from alembic import op

revision = "c062"
down_revision = "c061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE friendships ADD COLUMN IF NOT EXISTS restore_on_unblock "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE friendships ADD COLUMN IF NOT EXISTS mutual_block "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE friendships DROP COLUMN IF EXISTS mutual_block")
    op.execute("ALTER TABLE friendships DROP COLUMN IF EXISTS restore_on_unblock")
