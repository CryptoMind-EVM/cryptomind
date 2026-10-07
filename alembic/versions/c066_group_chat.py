"""群組聊天（c066）

2026-10-01 DANNY：Pro 小群私聊（設計 docs/plans/2026-10-01-group-chat-design.md）。
私訊表是寫死一對一的，群組另開六張表、私訊不動。
- 可見範圍：group_members.first_visible_message_id，新成員預設看不到入群前的訊息；
  群主開 history_visible 後才入群的人從 0 開始看。
- 已讀：每人一個 last_read_message_id（只增不減），「已讀 N」由它算，不逐則存。
- 群主帳號被刪：owner_id 設 NULL、群照常聊天（不 CASCADE，免得一個人的帳號連帶刪掉整群）。
- 檢舉跟私訊一樣在檢舉當下留快照（收回會清空原文）。
Rollback：``alembic downgrade c065``（刪掉六張表，群組資料全部消失）。
"""

from alembic import op

revision = "c066"
down_revision = "c065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_chats (
            id               SERIAL PRIMARY KEY,
            name             TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 30),
            owner_id         TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            history_visible  BOOLEAN NOT NULL DEFAULT FALSE,
            last_message_id  INTEGER,
            last_message_at  TIMESTAMPTZ,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_members (
            group_id                  INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            user_id                   TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            joined_at                 TIMESTAMPTZ DEFAULT NOW(),
            first_visible_message_id  INTEGER NOT NULL DEFAULT 0,
            last_read_message_id      INTEGER NOT NULL DEFAULT 0,
            muted                     BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (group_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_messages (
            id                   SERIAL PRIMARY KEY,
            group_id             INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            from_user_id         TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            content              TEXT NOT NULL,
            message_type         TEXT NOT NULL DEFAULT 'text'
                                 CHECK (message_type IN ('text', 'recalled', 'system')),
            reply_to_message_id  INTEGER REFERENCES group_messages(id) ON DELETE SET NULL,
            created_at           TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_messages_group ON group_messages(group_id, id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_message_reactions (
            id          SERIAL PRIMARY KEY,
            message_id  INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reaction    TEXT NOT NULL CHECK (reaction IN
                ('like', 'love', 'haha', 'wow', 'sad', 'rocket', 'diamond', 'ok')),
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_group_message_reaction UNIQUE (message_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_invites (
            id            SERIAL PRIMARY KEY,
            group_id      INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            inviter_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            invitee_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            status        TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            responded_at  TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_invite_pending "
        "ON group_invites(group_id, invitee_id) WHERE status = 'pending'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_invites_inviter ON group_invites(inviter_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_reports (
            id                SERIAL PRIMARY KEY,
            reporter_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reported_user_id  TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            group_id          INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            message_id        INTEGER REFERENCES group_messages(id) ON DELETE SET NULL,
            reason            TEXT NOT NULL CHECK (reason IN ('scam', 'harassment', 'spam', 'other')),
            note              TEXT,
            snapshot          JSONB NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'resolved', 'dismissed')),
            admin_note        TEXT,
            risk_score        REAL,
            risk_category     TEXT,
            resolved_by       TEXT,
            resolved_at       TIMESTAMPTZ,
            created_at        TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_group_report UNIQUE (reporter_user_id, message_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_reports_status ON group_reports(status, created_at)"
    )


def downgrade() -> None:
    for table in (
        "group_reports",
        "group_invites",
        "group_message_reactions",
        "group_messages",
        "group_members",
        "group_chats",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
