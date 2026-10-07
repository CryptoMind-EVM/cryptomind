"""
Database Schema Definitions
Contains all DDL statements for table creation
"""

import asyncio
import json
import logging
import re

logger = logging.getLogger(__name__)


def _add_report_risk_columns(c, table):
    """c065：檢舉附內容檢查的風險分數（後台排序用）。"""
    _run_reconcile_steps(
        c,
        table,
        [
            ("risk_score", f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS risk_score REAL"),
            ("risk_category", f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS risk_category TEXT"),
        ],
    )


def _run_in_savepoint(c, label, fn):
    """reconcile 的每一步包在自己的 SAVEPOINT：失敗只退回那一步。

    2026-10-01 前沒有包：PostgreSQL 一個語句失敗整個交易就進入 aborted，例外被各步自己吞掉，
    後面每個語句都靜靜失敗，init_db 最後的 commit 其實是 rollback——在已初始化過的 DB
    （含正式站）上整批 reconcile 從來沒生效過（reconcile_check_constraints 撞 DuplicateObject）。
    """
    savepoint = "reconcile_" + re.sub(r"[^a-z0-9_]", "_", label.lower())
    c.execute(f"SAVEPOINT {savepoint}")
    try:
        result = fn(c)
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:  # noqa: BLE001 — 單步失敗不擋啟動、不拖累其他步
        c.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        logger.warning("Schema reconcile step %s failed (rolled back): %s", label, e)
        return []
    c.execute(f"RELEASE SAVEPOINT {savepoint}")
    return result


def _run_reconcile_steps(c, table_name, steps):
    """Run idempotent reconcile SQL for legacy tables and report what was checked."""
    checked_items = []
    for label, statement in steps:
        c.execute(statement)
        checked_items.append(label)

    if checked_items:
        logger.info(
            "Schema reconcile checked %s: %s", table_name, ", ".join(checked_items)
        )

    return checked_items


def create_basic_tables(c):
    """Create basic tables (system_cache, system_config, revoked_tokens)"""
    # 自選清單 2026-09-27 改成 user_watchlist（c056，見 create_user_watchlist_table）
    # 建立已撤銷 Token 黑名單表
    c.execute("""
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            token_hash TEXT PRIMARY KEY,
            revoked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            expires_at TIMESTAMP WITH TIME ZONE
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires
        ON revoked_tokens (expires_at)
    """)

    # 建立系統快取表 (System Cache)
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_cache (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 建立系統配置表 (System Config)
    c.execute("""
        CREATE TABLE IF NOT EXISTS system_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            value_type TEXT DEFAULT 'string',
            category TEXT DEFAULT 'general',
            description TEXT,
            is_public INTEGER DEFAULT 1,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)


def create_conversation_tables(c):
    """Create conversation tables (conversation_history, sessions)"""
    # 建立對話歷史表 (Conversation History)
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversation_history (
            id SERIAL PRIMARY KEY,
            session_id TEXT DEFAULT 'default',
            user_id TEXT DEFAULT 'local_user',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            metadata TEXT
        )
    """)

    # 建立對話會話表 (Sessions)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT DEFAULT 'local_user',
            title TEXT,
            is_pinned INTEGER DEFAULT 0,
            pin_order INTEGER,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # c067：既有表 CREATE 不會補欄位。在這裡補（create_all_tables 每步各自 commit）；
    # reconcile_existing_tables 整批同一個交易，前面有一步失敗就整批被 rollback，不能只靠它
    reconcile_session_tables(c)

    # 對話附圖（vision Phase 2，c040）：壓縮圖 bytea＋30 天保留＋隨 session 刪除
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_attachments (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            mime TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            data BYTEA NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_attachments_created_at "
        "ON chat_attachments (created_at)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_attachments_user_id "
        "ON chat_attachments (user_id)"
    )


def create_user_tables(c):
    """Create user-related tables (users, membership_payments, admin_broadcasts, login_attempts)"""
    # 建立用戶表 (Users) — 身份 = user_id（TON 用戶即錢包地址）
    # display_name: 使用者自訂暱稱（NULL = 未設定，前端 fallback 到 username）
    # display_name_updated_at: 24h 改名冷卻的時間錨點
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            auth_method TEXT DEFAULT 'ton_wallet',
            last_active_at TIMESTAMPTZ,
            membership_tier TEXT DEFAULT 'free',
            membership_expires_at TIMESTAMPTZ,
            role TEXT DEFAULT 'user',
            is_active BOOLEAN DEFAULT TRUE,
            current_session_id TEXT,
            language TEXT,
            display_name TEXT UNIQUE,
            display_name_updated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 建立會員支付記錄表 (Membership Payments)
    c.execute("""
        CREATE TABLE IF NOT EXISTS membership_payments (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            amount NUMERIC(18,4) NOT NULL,
            months INTEGER NOT NULL,
            tx_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # c041: 多鏈身份（multichain design）——錢包綁定表。
    # 一個帳號可綁多個鏈上地址；一個地址只能屬於一個帳號（PRIMARY KEY(chain, address)）。
    # 既有 TON 用戶由 scripts/backfill_user_wallets.sql 回填（dry-run 先行）。
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_wallets (
            chain TEXT NOT NULL,
            address TEXT NOT NULL,
            user_id TEXT NOT NULL,
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            verified_at TIMESTAMPTZ DEFAULT NOW(),
            bound_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (chain, address),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_wallets_user ON user_wallets(user_id)"
    )
    # c064：用 Email／Google（Reown 內嵌錢包）登入的帳號，給後台看人數與名單
    c.execute("""
        CREATE TABLE IF NOT EXISTS embedded_wallet_users (
            user_id       TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            address       TEXT NOT NULL,
            provider      TEXT NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            login_count   INTEGER NOT NULL DEFAULT 1
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_embedded_wallet_users_last_seen "
        "ON embedded_wallet_users (last_seen_at)"
    )

    # c041: 多 rail 付款訂單生命週期（multichain design Part C）。
    # 跨鏈同 hash 防重放以 UNIQUE(chain, onchain_ref) 為準；
    # membership_payments.tx_hash UNIQUE 保留（向後相容，見 impl Task A1）。
    c.execute("""
        CREATE TABLE IF NOT EXISTS payment_orders (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            plan TEXT NOT NULL,
            rail TEXT NOT NULL,
            chain TEXT,
            asset TEXT,
            fiat_amount_usd NUMERIC(18,4) NOT NULL,
            quoted_amount NUMERIC(20,9),
            rate NUMERIC(20,10),
            expires_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'pending',
            order_token TEXT NOT NULL,
            onchain_ref TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(chain, onchain_ref),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # 管理員廣播紀錄表 (Admin Broadcasts)
    c.execute("""
        CREATE TABLE IF NOT EXISTS admin_broadcasts (
            id SERIAL PRIMARY KEY,
            admin_user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            type TEXT DEFAULT 'announcement',
            recipient_count INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # 用戶 API Keys 表 (BYOK - Bring Your Own Key)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            model_selection TEXT,
            key_kind TEXT NOT NULL DEFAULT 'llm',
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, provider),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)

    # Telegram 綁定表 (Telegram Bindings) — Bot 用戶綁定到平台帳號
    c.execute("""
        CREATE TABLE IF NOT EXISTS telegram_bindings (
            telegram_id BIGINT PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            first_name TEXT,
            linked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            last_used_at TIMESTAMP WITH TIME ZONE,
            active_session_id TEXT,
            UNIQUE(user_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_telegram_bindings_user ON telegram_bindings(user_id)"
    )

    # LINE 綁定表 — 與 telegram_bindings 同構（2026-09-08）。
    # 刻意鏡像而非在 users 加欄位：跨端共用 session 靠的是 active_session_id，
    # 單一個 users.line_user_id 欄位承載不了（同 telegram 的既有作法）。
    # PK 是 TEXT 不是 BIGINT——LINE 的 userId 是 33 字元字串（U + 32 hex），
    # 不是 Telegram 那種數字 id。
    c.execute("""
        CREATE TABLE IF NOT EXISTS line_bindings (
            line_user_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            display_name TEXT,
            linked_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            last_used_at TIMESTAMP WITH TIME ZONE,
            active_session_id TEXT,
            UNIQUE(user_id),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_line_bindings_user ON line_bindings(user_id)"
    )


def create_user_feedback_tables(c):
    """Create user feedback storage table."""
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_feedback (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            username TEXT,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_feedback_user_id
        ON user_feedback (user_id)
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at
        ON user_feedback (created_at DESC)
    """)


def create_forum_tables(c):
    """Create forum-related tables (boards, posts, forum_comments, tips, tags, post_tags, daily counts)"""
    # 看板表
    c.execute("""
        CREATE TABLE IF NOT EXISTS boards (
            id              SERIAL PRIMARY KEY,
            name            TEXT NOT NULL,
            slug            TEXT NOT NULL UNIQUE,
            description     TEXT,
            post_count      INTEGER DEFAULT 0,
            is_active       INTEGER DEFAULT 1,
            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 文章表
    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id              SERIAL PRIMARY KEY,
            board_id        INTEGER NOT NULL,
            user_id         TEXT NOT NULL,
            category        TEXT NOT NULL,
            title           TEXT NOT NULL,
            content         TEXT NOT NULL,
            tags            TEXT,

            push_count      INTEGER DEFAULT 0,
            boo_count       INTEGER DEFAULT 0,
            comment_count   INTEGER DEFAULT 0,
            tips_total      NUMERIC(18,4) DEFAULT 0,
            view_count      INTEGER DEFAULT 0,

            payment_tx_hash TEXT,

            is_pinned       INTEGER DEFAULT 0,
            is_hidden       INTEGER DEFAULT 0,

            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (board_id) REFERENCES boards(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 回覆表
    c.execute("""
        CREATE TABLE IF NOT EXISTS forum_comments (
            id              SERIAL PRIMARY KEY,
            post_id         INTEGER NOT NULL,
            user_id         TEXT NOT NULL,
            parent_id       INTEGER,
            type            TEXT NOT NULL,
            content         TEXT,

            is_hidden       INTEGER DEFAULT 0,

            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (post_id) REFERENCES posts(id),
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (parent_id) REFERENCES forum_comments(id)
        )
    """)

    # 打賞記錄表
    c.execute("""
        CREATE TABLE IF NOT EXISTS tips (
            id              SERIAL PRIMARY KEY,
            post_id         INTEGER NOT NULL,
            from_user_id    TEXT NOT NULL,
            to_user_id      TEXT NOT NULL,
            amount          NUMERIC(18,4) NOT NULL DEFAULT 1,
            tx_hash         TEXT NOT NULL UNIQUE,

            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (post_id) REFERENCES posts(id),
            FOREIGN KEY (from_user_id) REFERENCES users(user_id),
            FOREIGN KEY (to_user_id) REFERENCES users(user_id)
        )
    """)

    # 標籤統計表
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id              SERIAL PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            post_count      INTEGER DEFAULT 0,
            last_used_at    TIMESTAMPTZ,

            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 文章標籤關聯表
    c.execute("""
        CREATE TABLE IF NOT EXISTS post_tags (
            post_id         INTEGER NOT NULL,
            tag_id          INTEGER NOT NULL,

            PRIMARY KEY (post_id, tag_id),
            FOREIGN KEY (post_id) REFERENCES posts(id),
            FOREIGN KEY (tag_id) REFERENCES tags(id)
        )
    """)

    # 用戶每日回覆計數表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_daily_comments (
            id              SERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            date            DATE NOT NULL,
            comment_count   INTEGER DEFAULT 0,

            UNIQUE (user_id, date),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 用戶每日發文計數表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_daily_posts (
            id              SERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            date            DATE NOT NULL,
            post_count      INTEGER DEFAULT 0,

            UNIQUE (user_id, date),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)


def create_scam_tracker_tables(c):
    """Create scam tracker tables (scam_reports, votes, comments)"""
    # 詐騙舉報表
    c.execute("""
        CREATE TABLE IF NOT EXISTS scam_reports (
            id SERIAL PRIMARY KEY,

            -- 錢包資訊
            scam_wallet_address TEXT NOT NULL UNIQUE,

            -- 舉報者資訊
            reporter_user_id TEXT NOT NULL,
            reporter_wallet_masked TEXT NOT NULL,

            -- 詐騙資訊
            scam_type TEXT NOT NULL,
            description TEXT NOT NULL,
            transaction_hash TEXT,

            -- 驗證狀態
            verification_status TEXT DEFAULT 'pending',

            -- 社群投票統計
            approve_count INTEGER DEFAULT 0,
            reject_count INTEGER DEFAULT 0,

            -- 元數據
            comment_count INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,

            -- 時間戳
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            -- 外鍵
            FOREIGN KEY (reporter_user_id) REFERENCES users(user_id)
        )
    """)

    # 投票表
    c.execute("""
        CREATE TABLE IF NOT EXISTS scam_report_votes (
            id SERIAL PRIMARY KEY,
            report_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            vote_type TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            UNIQUE(report_id, user_id),
            FOREIGN KEY (report_id) REFERENCES scam_reports(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 評論表
    c.execute("""
        CREATE TABLE IF NOT EXISTS scam_report_comments (
            id SERIAL PRIMARY KEY,
            report_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            content TEXT NOT NULL,
            transaction_hash TEXT,
            is_hidden INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (report_id) REFERENCES scam_reports(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)


def create_friendship_tables(c):
    """Create friendship tables"""
    # 好友關係表
    c.execute("""
        CREATE TABLE IF NOT EXISTS friendships (
            id              SERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            friend_id       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (friend_id) REFERENCES users(user_id),
            UNIQUE (user_id, friend_id)
        )
    """)
    # c062：封鎖列記「封鎖前是好友」與「對方也封鎖了」。既有表 CREATE 不會補欄位，自己補
    c.execute(
        "ALTER TABLE friendships ADD COLUMN IF NOT EXISTS restore_on_unblock "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    c.execute(
        "ALTER TABLE friendships ADD COLUMN IF NOT EXISTS mutual_block "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )


def create_dm_tables(c):
    """Create direct message tables"""
    # 對話表（兩人之間的對話）
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_conversations (
            id                  SERIAL PRIMARY KEY,
            user1_id            TEXT NOT NULL,
            user2_id            TEXT NOT NULL,
            last_message_id     INTEGER,
            last_message_at     TIMESTAMPTZ,
            user1_unread_count  INTEGER DEFAULT 0,
            user2_unread_count  INTEGER DEFAULT 0,
            created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            UNIQUE (user1_id, user2_id),
            FOREIGN KEY (user1_id) REFERENCES users(user_id),
            FOREIGN KEY (user2_id) REFERENCES users(user_id)
        )
    """)

    # 私訊訊息表
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_messages (
            id              SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL,
            from_user_id    TEXT NOT NULL,
            to_user_id      TEXT NOT NULL,
            content         TEXT NOT NULL,
            message_type    TEXT DEFAULT 'text',
            is_read         INTEGER DEFAULT 0,
            read_at         TIMESTAMPTZ,
            created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

            reply_to_message_id INTEGER,

            FOREIGN KEY (conversation_id) REFERENCES dm_conversations(id),
            FOREIGN KEY (from_user_id) REFERENCES users(user_id),
            FOREIGN KEY (to_user_id) REFERENCES users(user_id),
            FOREIGN KEY (reply_to_message_id) REFERENCES dm_messages(id) ON DELETE SET NULL
        )
    """)
    # 回覆引用（c059）：既有表的 CREATE TABLE IF NOT EXISTS 不會補欄位，reconcile 自己補
    c.execute(
        "ALTER TABLE dm_messages ADD COLUMN IF NOT EXISTS reply_to_message_id "
        "INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL"
    )

    # 私訊表情回應（c060）：每人每則一個，DB 只存 key（core.dm_reactions.REACTION_KEYS）
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_message_reactions (
            id          SERIAL PRIMARY KEY,
            message_id  INTEGER NOT NULL REFERENCES dm_messages(id) ON DELETE CASCADE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reaction    TEXT NOT NULL CHECK (reaction IN
                ('like', 'love', 'haha', 'wow', 'sad', 'rocket', 'diamond', 'ok')),
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_dm_message_reaction UNIQUE (message_id, user_id)
        )
    """)

    # 私訊檢舉（c061）：快照留證據，只有管理員看得到
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_reports (
            id                SERIAL PRIMARY KEY,
            reporter_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reported_user_id  TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            conversation_id   INTEGER NOT NULL REFERENCES dm_conversations(id) ON DELETE CASCADE,
            message_id        INTEGER REFERENCES dm_messages(id) ON DELETE SET NULL,
            reason            TEXT NOT NULL CHECK (reason IN ('scam', 'harassment', 'spam', 'other')),
            note              TEXT,
            snapshot          JSONB NOT NULL,
            status            TEXT NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'resolved', 'dismissed')),
            admin_note        TEXT,
            resolved_by       TEXT,
            resolved_at       TIMESTAMPTZ,
            created_at        TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_dm_report UNIQUE (reporter_user_id, message_id)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_reports_status ON dm_reports(status, created_at)"
    )
    _add_report_risk_columns(c, "dm_reports")

    # 私訊刪除記錄表（只對自己隱藏，不影響對方）
    c.execute("""
        CREATE TABLE IF NOT EXISTS dm_message_deletions (
            id              SERIAL PRIMARY KEY,
            message_id      INTEGER NOT NULL,
            user_id         TEXT NOT NULL,

            UNIQUE (message_id, user_id),
            FOREIGN KEY (message_id) REFERENCES dm_messages(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)

    # 用戶訊息限制追蹤表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_message_limits (
            id              SERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            date            DATE NOT NULL,
            message_count   INTEGER DEFAULT 0,
            greeting_count  INTEGER DEFAULT 0,
            greeting_month  TEXT,

            UNIQUE (user_id, date),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    """)


def create_group_chat_tables(c):
    """群組聊天（c066，設計 docs/plans/2026-10-01-group-chat-design.md）。
    私訊表寫死一對一，群組另開六張表。新成員看得到 id > first_visible_message_id 的訊息；
    已讀是每人一個 last_read_message_id；群主帳號被刪 owner_id 設 NULL（不連帶刪群）。
    dissolved_at（c068）：群主解散，成員全部移出、訊息留著當檢舉證據。"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_chats (
            id               SERIAL PRIMARY KEY,
            name             TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 30),
            owner_id         TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            history_visible  BOOLEAN NOT NULL DEFAULT FALSE,
            last_message_id  INTEGER,
            last_message_at  TIMESTAMPTZ,
            dissolved_at     TIMESTAMPTZ,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # c068：既有表 CREATE 不會補欄位，在這裡補（create_all_tables 每步各自 commit）；
    # 啟動 reconcile 也會補一次（SAFE_RECONCILE_STEPS 的 group_chats）
    reconcile_group_chat_tables(c)
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            group_id                  INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            user_id                   TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            joined_at                 TIMESTAMPTZ DEFAULT NOW(),
            first_visible_message_id  INTEGER NOT NULL DEFAULT 0,
            last_read_message_id      INTEGER NOT NULL DEFAULT 0,
            muted                     BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (group_id, user_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_messages (
            id                   SERIAL PRIMARY KEY,
            group_id             INTEGER NOT NULL REFERENCES group_chats(id) ON DELETE CASCADE,
            from_user_id         TEXT REFERENCES users(user_id) ON DELETE SET NULL,
            content              TEXT NOT NULL,
            message_type         TEXT NOT NULL DEFAULT 'text'
                                 CHECK (message_type IN ('text', 'recalled', 'system', 'ai_card')),
            reply_to_message_id  INTEGER REFERENCES group_messages(id) ON DELETE SET NULL,
            created_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_group_messages_group ON group_messages(group_id, id)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS group_message_reactions (
            id          SERIAL PRIMARY KEY,
            message_id  INTEGER NOT NULL REFERENCES group_messages(id) ON DELETE CASCADE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            reaction    TEXT NOT NULL CHECK (reaction IN
                ('like', 'love', 'haha', 'wow', 'sad', 'rocket', 'diamond', 'ok')),
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_group_message_reaction UNIQUE (message_id, user_id)
        )
    """)
    c.execute("""
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
    """)
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_group_invite_pending "
        "ON group_invites(group_id, invitee_id) WHERE status = 'pending'"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_invites_inviter ON group_invites(inviter_id, created_at)"
    )
    c.execute("""
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
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_reports_status ON group_reports(status, created_at)"
    )


def create_chat_pins_table(c):
    """對話置頂（c067）：私訊與群組共用，target_id 依 kind 指 dm_conversations.id 或 group_chats.id
    （多型，不設 FK）。position 越小越上面；退群留下的失效置頂由 chat_pins_repo 每次操作時清掉。"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_pins (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            position    INTEGER NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, kind, target_id)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_pins_user_position ON chat_pins(user_id, position)"
    )


def create_chat_order_table(c):
    """對話自訂順序（c068）：社群列表「自訂順序」模式的位置，私訊與群組共用一個序列（0..n-1）。
    target_id 依 kind 指 dm_conversations.id 或 group_chats.id（多型，不設 FK）；
    置頂的照 chat_pins 排，這裡的位置只在沒置頂時用。失效的列由 chat_order_repo 排序時清掉。"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_order (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            position    INTEGER NOT NULL,
            PRIMARY KEY (user_id, kind, target_id)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_order_user_position ON chat_order(user_id, position)"
    )


def create_chat_assistant_history_table(c):
    """聊天室 AI 助理的問答紀錄（c070）：只有提問的人看得到，跨裝置接續。每人每個聊天室留最近
    10 則、7 天（chat_assistant_history_repo）。target_id 依 kind 指 dm_conversations.id 或
    group_chats.id（多型，不設 FK）；source_ids＝回答讀過的訊息 id，其中任一則被收回就作廢。"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS chat_assistant_history (
            id          BIGSERIAL PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL CHECK (kind IN ('dm', 'group')),
            target_id   INTEGER NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            source_ids  INTEGER[] NOT NULL DEFAULT '{}',
            meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_assistant_history_chat "
        "ON chat_assistant_history(user_id, kind, target_id, id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_assistant_history_sources "
        "ON chat_assistant_history USING GIN (source_ids)"
    )


def create_shared_answers_table(c):
    """AI 回答快照分享（c072）：使用者選的那一輪問答（已遮蔽錢包地址等），免登入唯讀公開頁。
    token 不存、只存 SHA-256；到期（expires_at）或撤銷（revoked_at）後讀不到；users 刪除時 CASCADE。
    整個功能由旗標 CONVERSATION_SHARE_ENABLED 控制（預設關）。"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS shared_answers (
            id          BIGSERIAL PRIMARY KEY,
            token_hash  TEXT NOT NULL UNIQUE,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked_at  TIMESTAMPTZ
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_answers_user "
        "ON shared_answers(user_id, created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_answers_expires "
        "ON shared_answers(expires_at)"
    )


def create_audit_log_tables(c):
    """Create audit log tables"""
    # 審計日誌主表 - 記錄所有安全敏感操作
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

            -- User information
            user_id VARCHAR(255),
            username VARCHAR(255),

            -- Action details
            action VARCHAR(100) NOT NULL,
            resource_type VARCHAR(100),
            resource_id VARCHAR(255),

            -- Request details
            endpoint VARCHAR(255) NOT NULL,
            method VARCHAR(10) NOT NULL,
            ip_address VARCHAR(45),
            user_agent TEXT,

            -- Request/Response data
            request_data JSONB,
            response_code INTEGER,

            -- Status
            success BOOLEAN DEFAULT TRUE,
            error_message TEXT,

            -- Performance
            duration_ms INTEGER,

            -- Additional metadata
            metadata JSONB,

            -- Timestamps
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)


def create_governance_tables(c):
    """Create community governance tables"""
    # 內容檢舉表
    c.execute("""
        CREATE TABLE IF NOT EXISTS content_reports (
            id SERIAL PRIMARY KEY,
            content_type VARCHAR(20) NOT NULL,
            content_id INTEGER NOT NULL,
            reporter_user_id VARCHAR(255) NOT NULL,
            report_type VARCHAR(50) NOT NULL,
            description TEXT,
            review_status VARCHAR(20) DEFAULT 'pending',
            violation_level VARCHAR(20),
            approve_count INTEGER DEFAULT 0,
            reject_count INTEGER DEFAULT 0,
            points_assigned INTEGER DEFAULT 0,
            action_taken VARCHAR(50),
            processed_by VARCHAR(255),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    _add_report_risk_columns(c, "content_reports")

    # 檢舉審核投票表
    c.execute("""
        CREATE TABLE IF NOT EXISTS report_review_votes (
            id SERIAL PRIMARY KEY,
            report_id INTEGER NOT NULL REFERENCES content_reports(id),
            reviewer_user_id VARCHAR(255) NOT NULL,
            vote_type VARCHAR(20) NOT NULL,
            vote_weight FLOAT DEFAULT 1.0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(report_id, reviewer_user_id)
        )
    """)

    # 用戶違規記錄表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_violations (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            violation_level VARCHAR(20) NOT NULL,
            violation_type VARCHAR(50),
            points INTEGER DEFAULT 0,
            source_type VARCHAR(20),
            source_id INTEGER,
            action_taken VARCHAR(50),
            suspended_until TIMESTAMPTZ,
            processed_by VARCHAR(255),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # 用戶違規點數表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_violation_points (
            user_id VARCHAR(255) PRIMARY KEY,
            points INTEGER DEFAULT 0,
            total_violations INTEGER DEFAULT 0,
            last_violation_at TIMESTAMPTZ,
            suspension_count INTEGER DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # 審核信譽表
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_reputation (
            user_id VARCHAR(255) PRIMARY KEY,
            total_reviews INTEGER DEFAULT 0,
            correct_votes INTEGER DEFAULT 0,
            accuracy_rate FLOAT DEFAULT 0.0,
            reputation_score INTEGER DEFAULT 0,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # 用戶活動日誌表
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            activity_type VARCHAR(100) NOT NULL,
            resource_type VARCHAR(50),
            resource_id INTEGER,
            metadata JSONB,
            success BOOLEAN DEFAULT TRUE,
            error_message TEXT,
            ip_address VARCHAR(45),
            user_agent TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def create_analysis_tables(c):
    """Create analysis report tables"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS analysis_reports (
            id SERIAL PRIMARY KEY,
            session_id VARCHAR(255),
            user_id VARCHAR(255),
            symbol VARCHAR(50),
            interval VARCHAR(10) DEFAULT '1d',
            report_text TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    # Codebook feedback table for analysis quality scoring
    c.execute("""
        CREATE TABLE IF NOT EXISTS codebook_feedback (
            id SERIAL PRIMARY KEY,
            codebook_entry_id TEXT NOT NULL,
            user_id VARCHAR(255),
            score INTEGER NOT NULL CHECK (score IN (0, 1)),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_reports_session ON analysis_reports(session_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_reports_user ON analysis_reports(user_id)"
    )


def create_price_alert_tables(c):
    """Create price alert tables"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            market      TEXT NOT NULL,
            condition   TEXT NOT NULL,
            target      NUMERIC(18,4) NOT NULL,
            repeat      INTEGER NOT NULL DEFAULT 0,
            triggered   INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL,
            CONSTRAINT fk_alert_user
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_alerts_user ON price_alerts(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_price_alerts_active ON price_alerts(triggered) WHERE triggered = 0"
    )


def create_tool_tables(c):
    """Create tool system tables"""
    # 工具目錄：所有可用工具的 Metadata
    c.execute("""
        CREATE TABLE IF NOT EXISTS tools_catalog (
            tool_id          TEXT PRIMARY KEY,
            display_name     TEXT NOT NULL,
            description      TEXT,
            category         TEXT NOT NULL,
            tier_required    TEXT DEFAULT 'free',
            quota_type       TEXT DEFAULT 'unlimited',
            daily_limit_free INTEGER DEFAULT 0,
            daily_limit_plus INTEGER,
            daily_limit_prem INTEGER,
            source_type      TEXT DEFAULT 'native',
            key_provider     TEXT,
            key_mode         TEXT DEFAULT 'none',
            risk_level       TEXT DEFAULT 'low',
            is_active        BOOLEAN DEFAULT TRUE,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Agent 可用工具設定（Admin 層控制）
    c.execute("""
        CREATE TABLE IF NOT EXISTS agent_tool_permissions (
            agent_id    TEXT NOT NULL,
            tool_id     TEXT NOT NULL,
            is_enabled  BOOLEAN DEFAULT TRUE,
            PRIMARY KEY (agent_id, tool_id)
        )
    """)

    # 用戶工具偏好（用戶層個人化，Premium 才可改）
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_tool_preferences (
            user_id     TEXT NOT NULL,
            tool_id     TEXT NOT NULL,
            is_enabled  BOOLEAN DEFAULT TRUE,
            updated_at  TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, tool_id)
        )
    """)

    # 用戶分析偏好（每個 agent 的 system prompt 和 enabled tools）
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_analysis_preferences (
            user_id       TEXT NOT NULL,
            agent_id      TEXT NOT NULL,
            system_prompt TEXT,
            enabled_tools TEXT[],
            updated_at    TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (user_id, agent_id)
        )
    """)

    # 工具每日使用量追蹤（Rate Limiting）
    c.execute("""
        CREATE TABLE IF NOT EXISTS tool_usage_log (
            user_id     TEXT NOT NULL,
            tool_id     TEXT NOT NULL,
            used_date   DATE NOT NULL DEFAULT CURRENT_DATE,
            call_count  INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, tool_id, used_date)
        )
    """)


def create_indexes(c):
    """Create all indexes for optimization"""
    # AI 對話歷史索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_history_session_timestamp ON conversation_history(session_id, timestamp)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_conversation_history_user_timestamp ON conversation_history(user_id, timestamp DESC)"
    )
    # ✅ 效能修復：sessions 表缺少 user_id index，導致 get_sessions() 全表掃描
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_updated_at ON sessions(updated_at DESC)"
    )
    # ✅ 效能修復：users 表常用查詢欄位補 index
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_last_active ON users(last_active_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_membership ON users(membership_tier)"
    )
    # ✅ 效能修復：membership_payments 補 user_id index
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_membership_payments_user ON membership_payments(user_id)"
    )
    # ✅ user_api_keys 索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON user_api_keys(user_id)"
    )

    # 論壇索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_board_id ON posts(board_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_user_id ON posts(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_created_at ON posts(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_category ON posts(category)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_forum_comments_post_id ON forum_comments(post_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_forum_comments_user_id ON forum_comments(user_id)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_tips_post_id ON tips(post_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tips_from_user ON tips(from_user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_tips_to_user ON tips(to_user_id)")
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_payment_tx_hash ON posts(payment_tx_hash) WHERE payment_tx_hash IS NOT NULL"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(name)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_daily_comments_user_date ON user_daily_comments(user_id, date)"
    )

    # 可疑錢包追蹤系統索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_scam_wallet ON scam_reports(scam_wallet_address)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_scam_type ON scam_reports(scam_type)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_scam_status ON scam_reports(verification_status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_scam_created ON scam_reports(created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_vote_report ON scam_report_votes(report_id)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_vote_user ON scam_report_votes(user_id)")

    # Payment deduplication（premium 會員付款 replay 防護；唯一使用者 premium.py）。
    c.execute("""
        CREATE TABLE IF NOT EXISTS used_payments (
            payment_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_used_payments_user ON used_payments(user_id)"
    )


    # Friend request deduplication (ordered pair)
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_friendships_ordered_pair ON friendships(LEAST(user_id, friend_id), GREATEST(user_id, friend_id))"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_comment_report ON scam_report_comments(report_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_comment_created ON scam_report_comments(created_at DESC)"
    )

    # 社群治理系統索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_reports_status ON content_reports(review_status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_reports_reporter ON content_reports(reporter_user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_reports_content ON content_reports(content_type, content_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_reports_created ON content_reports(created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_report_review_votes_report ON report_review_votes(report_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_violations_user ON user_violations(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_activity_logs_user ON user_activity_logs(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_activity_logs_type ON user_activity_logs(activity_type)"
    )

    # 好友功能索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_friendships_user_id ON friendships(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_friendships_friend_id ON friendships(friend_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_friendships_status ON friendships(status)"
    )

    # 私訊功能索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_conversations_user1 ON dm_conversations(user1_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_conversations_user2 ON dm_conversations(user2_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_conversations_last_message ON dm_conversations(last_message_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_conversation ON dm_messages(conversation_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_created ON dm_messages(created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_from_user ON dm_messages(from_user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_to_user ON dm_messages(to_user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_dm_messages_conversation_created ON dm_messages(conversation_id, created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_message_limits ON user_message_limits(user_id, date)"
    )

    # 工具系統索引
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_tools_catalog_category ON tools_catalog(category)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_tools_catalog_tier ON tools_catalog(tier_required)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_tool_agent ON agent_tool_permissions(agent_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_tool_prefs_user ON user_tool_preferences(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_usage_user_date ON tool_usage_log(user_id, used_date)"
    )

    # 記憶系統索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_memory_user ON user_memory(user_id)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_memory_session ON user_memory(session_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_memory_type ON user_memory(memory_type)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_history_user ON user_history_log(user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_history_session ON user_history_log(session_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_history_created ON user_history_log(created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_success_timestamp ON audit_logs(success, timestamp DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_users_premium ON users(membership_tier) WHERE membership_tier IN ('pro', 'premium')"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_friendships_user_friend_status ON friendships(user_id, friend_id, status)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_friendships_friend_user_status ON friendships(friend_id, user_id, status)"
    )


def init_default_data(c):
    """Initialize default data (boards, system config)"""
    from core.config import FORUM_LIMITS, TON_PAYMENT_PRICES

    # 初始化預設看板（如果不存在）
    c.execute("SELECT COUNT(*) FROM boards WHERE slug = 'crypto'")
    if c.fetchone()[0] == 0:
        c.execute("""
            INSERT INTO boards (name, slug, description, is_active)
            VALUES ('加密貨幣', 'crypto', '加密貨幣相關討論', 1)
        """)

    # 初始化系統配置（如果不存在）
    default_configs = [
        # 價格配置（單位：TON）
        (
            "price_create_post",
            str(TON_PAYMENT_PRICES.get("create_post", 0.1)),
            "float",
            "pricing",
            "發文費用 (TON)",
            1,
        ),
        (
            "price_tip",
            str(TON_PAYMENT_PRICES.get("tip", 0.1)),
            "float",
            "pricing",
            "打賞費用 (TON)",
            1,
        ),
        (
            "price_premium",
            str(TON_PAYMENT_PRICES.get("premium_monthly", 1.0)),
            "float",
            "pricing",
            "Premium 會員費用 (TON)",
            1,
        ),
        # 論壇限制配置
        (
            "limit_daily_post_free",
            str(FORUM_LIMITS.get("daily_post_free", 3)),
            "int",
            "limits",
            "一般會員每日發文上限",
            1,
        ),
        (
            "limit_daily_post_premium",
            "null",
            "int",
            "limits",
            "Premium 會員每日發文上限 (null=無限)",
            1,
        ),
        (
            "limit_daily_comment_free",
            str(FORUM_LIMITS.get("daily_comment_free", 20)),
            "int",
            "limits",
            "一般會員每日回覆上限",
            1,
        ),
        (
            "limit_daily_comment_premium",
            "null",
            "int",
            "limits",
            "Premium 會員每日回覆上限 (null=無限)",
            1,
        ),
        # 私訊限制配置
        ("limit_daily_message_free", "20", "int", "limits", "一般會員每日私訊上限", 1),
        (
            "limit_daily_message_premium",
            "null",
            "int",
            "limits",
            "Premium 會員每日私訊上限 (null=無限)",
            1,
        ),
        (
            "limit_monthly_greeting",
            "5",
            "int",
            "limits",
            "Premium 會員每月打招呼上限",
            1,
        ),
        ("limit_message_max_length", "500", "int", "limits", "單則訊息最大字數", 1),
        # 群組聊天（c066）：開關預設關，DANNY 線上驗收後在後台打開
        ("group_chat_enabled", "false", "bool", "features", "群組聊天功能開關", 1),
        ("limit_group_create", "5", "int", "limits", "每人最多建立幾個群組（自己當群主）", 1),
        ("limit_group_join", "20", "int", "limits", "每人最多加入幾個群組（含自己建的）", 1),
        ("limit_group_members", "50", "int", "limits", "每個群組最多幾人", 1),
        ("limit_daily_group_invites", "30", "int", "limits", "每人每天最多送出幾個群組邀請", 1),
        # 聊天室 AI 助理（docs/plans/2026-10-01-chat-assistant-design.md）：開關預設關
        (
            "chat_assistant_enabled",
            "false",
            "bool",
            "features",
            "聊天室 AI 助理開關",
            1,
        ),
        (
            "limit_ai_assistant_free_daily",
            "5",
            "int",
            "limits",
            "非 Pro 每天在私訊用 AI 助理的次數（平台模型；自帶金鑰不限）",
            1,
        ),
        # 可疑錢包追蹤配置
        (
            "scam_report_daily_limit_pro",
            "5",
            "int",
            "scam_tracker",
            "Premium 用戶每日可舉報可疑錢包數量",
            1,
        ),
        (
            "scam_comment_require_pro",
            "true",
            "bool",
            "scam_tracker",
            "評論是否僅限 Premium 用戶",
            1,
        ),
        (
            "scam_verification_vote_threshold",
            "10",
            "int",
            "scam_tracker",
            "達到「已驗證」所需的最低總投票數",
            1,
        ),
        (
            "scam_verification_approve_rate",
            "0.7",
            "float",
            "scam_tracker",
            "達到「已驗證」所需的贊同率（0-1）",
            1,
        ),
        (
            "scam_wallet_mask_length",
            "4",
            "int",
            "scam_tracker",
            "錢包地址遮罩顯示長度（前後各保留字符數）",
            1,
        ),
        ("scam_list_page_size", "20", "int", "scam_tracker", "列表每頁顯示數量", 1),
    ]

    # 詐騙類型配置（JSON）
    scam_types_config = json.dumps(
        [
            {"id": "fake_official", "name": "假冒官方", "icon": "🎭"},
            {"id": "investment_scam", "name": "投資詐騙", "icon": "💰"},
            {"id": "fake_airdrop", "name": "空投詐騙", "icon": "🎁"},
            {"id": "trading_fraud", "name": "交易詐騙", "icon": "🔄"},
            {"id": "gambling", "name": "賭博騙局", "icon": "🎰"},
            {"id": "phishing", "name": "釣魚網站", "icon": "🎣"},
            {"id": "other", "name": "其他詐騙", "icon": "⚠️"},
        ],
        ensure_ascii=False,
    )

    default_configs.append(
        (
            "scam_types",
            scam_types_config,
            "json",
            "scam_tracker",
            "詐騙類型列表（可動態新增）",
            1,
        )
    )

    for key, value, value_type, category, description, is_public in default_configs:
        c.execute("SELECT COUNT(*) FROM system_config WHERE key = %s", (key,))
        if c.fetchone()[0] == 0:
            c.execute(
                """
                INSERT INTO system_config (key, value, value_type, category, description, is_public)
                VALUES (%s, %s, %s, %s, %s, %s)
            """,
                (key, value, value_type, category, description, is_public),
            )


def create_memory_tables(c):
    """Create user memory tables for persistent agent memory"""
    # 用戶長期記憶表 (MEMORY.md equivalent)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            session_id VARCHAR(255),
            memory_type VARCHAR(20) NOT NULL DEFAULT 'long_term',
            content TEXT,
            agent_id TEXT,
            scope TEXT NOT NULL DEFAULT 'shared',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

            UNIQUE(user_id, session_id, memory_type)
        )
    """)
    # Mixer Step 4（c043）：agent 維度索引。**不動**舊 UNIQUE(user_id,
    # session_id, memory_type)——它同時是 write_long_term／write_compact_state
    # 的 ON CONFLICT arbiter，換成 expression index 會讓 Postgres 無法推導
    # arbiter → consolidation 靜默死亡（review P0）。agent 維度唯一性手術
    # 留給 Step 5 真正寫 agent blob 時連同 arbiter 一起換。
    # 2026-09-05：既有表的 CREATE TABLE IF NOT EXISTS 是 no-op，**不會補欄位**。
    # 少了這兩行，舊 DB 上的 user_memory 沒有 agent_id，下面的索引直接炸，
    # 整個 memory reconcile step 掛掉（production 實際症狀：
    # "Schema step 'memory' failed: column agent_id does not exist"）。
    # 而那兩欄本來只能靠 c043 加，c043 又被 DuplicateTable 擋住——兩套自癒
    # 系統互鎖。reconcile 必須自己補得起既有表。
    c.execute("ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS agent_id TEXT")
    c.execute(
        "ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS "
        "scope TEXT NOT NULL DEFAULT 'shared'"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_memory_agent "
        "ON user_memory(user_id, agent_id)"
    )

    # 用戶對話歷史日誌表 (HISTORY.md equivalent)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_history_log (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            session_id VARCHAR(255),
            entry TEXT NOT NULL,
            tools_used TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)

    # 用戶記憶快取表 (for fast access)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_memory_cache (
            user_id VARCHAR(255) PRIMARY KEY,
            session_id VARCHAR(255),
            last_consolidated_index INTEGER DEFAULT 0,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)


def create_user_facts_table(c):
    """Create user_facts table for nanoclaw-style structured fact extraction.

    c013(2026-07-25):加 created_at/last_accessed/access_count 欄位,
    支援 Hermes 式記憶品質管控(過時事實淘汰、熱門事實排序)。

    c014(2026-07-31):加 category 欄位,支援 remember 工具的分類記憶
    (fact/preference/holding/context),preference/holding 優先注入 system prompt。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id SERIAL PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            key VARCHAR(100) NOT NULL,
            value TEXT NOT NULL,
            confidence VARCHAR(10) DEFAULT 'high',
            source_turn INTEGER,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            access_count INTEGER NOT NULL DEFAULT 0,
            category VARCHAR(20) DEFAULT 'fact',

            UNIQUE(user_id, key)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id)")
    # 先補欄位（針對 c013/c014 之前建立的舊表），再建 category 索引；
    # 順序反了會讓舊表（缺 category 欄位）在這裡就炸掉整個 user_facts step。
    reconcile_user_facts_columns(c)


def reconcile_user_facts_columns(c):
    """為既有 user_facts 表補上 c013/c014 新欄位(自癒,belt-and-suspenders)。

    即使 alembic 卡住,啟動時也能補上 created_at/last_accessed/access_count/category,
    讓記憶品質管控不會因 schema 落後而靜默失效。
    """
    _run_reconcile_steps(
        c,
        "user_facts",
        [
            ("created_at",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()"),
            ("last_accessed",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "last_accessed TIMESTAMP WITH TIME ZONE DEFAULT NOW()"),
            ("access_count",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "access_count INTEGER NOT NULL DEFAULT 0"),
            ("category",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "category VARCHAR(20) DEFAULT 'fact'"),
            # c031 記憶治理（docs/plans/2026-08-21-memory-governance-design.md）
            ("valid_from",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "valid_from TIMESTAMPTZ DEFAULT NOW()"),
            ("valid_until",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "valid_until TIMESTAMPTZ DEFAULT NULL"),
            ("source_query",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "source_query TEXT DEFAULT ''"),
            ("supersedes_id",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "supersedes_id INTEGER DEFAULT NULL"),
            ("status",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "status VARCHAR(20) DEFAULT 'active'"),
            ("verified_at",
             "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS "
             "verified_at TIMESTAMPTZ DEFAULT NULL"),
        ],
    )
    # c014: category 索引（自癒補建）
    try:
        c.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_facts_user_category "
            "ON user_facts(user_id, category)"
        )
    except Exception:
        pass  # 索引建失敗不阻塞啟動


def create_user_facts_private_table(c):
    """Mixer Step 4（c043）：agent 私有事實層。

    共享表 user_facts 刻意不動（免 constraint 手術）——私有層實體分離，
    隔離邊界可直接審計；rollback＝DROP TABLE。與 c043 migration 三處同步
    （ORM：UserFactPrivate）。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_facts_private (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence TEXT DEFAULT 'high',
            source_turn INTEGER,
            category TEXT DEFAULT 'fact',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

            CONSTRAINT uq_user_fact_private UNIQUE (user_id, agent_id, key)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_fact_private_agent "
        "ON user_facts_private(user_id, agent_id)"
    )


# ============================================================================
# c015(2026-07-31): Skill 管理 — 使用者開關官方 skill + 自訂 skill
# ============================================================================


def create_user_skill_overrides_table(c):
    """使用者對官方 skill 的開關偏好（per-user）。

    官方 skill 不可修改內容，但使用者可關閉（關閉 = 不注入該 skill）。
    仿 user_tool_preferences 模式。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_skill_overrides (
            user_id     TEXT NOT NULL,
            skill_name  TEXT NOT NULL,
            is_enabled  BOOLEAN DEFAULT TRUE,
            updated_at  TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, skill_name)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_skill_overrides_user "
        "ON user_skill_overrides(user_id)"
    )


def create_user_llm_preferences_table(c):
    """使用者選的 LLM provider（跨裝置帶回）。

    per-user 單列：記「使用者整體選了哪個 provider」（如 deepseek）。
    per-provider 的「上次用的 model」由 user_api_keys.model_selection 負責，
    兩者組合還原「上次用了 deepseek + v4-flash」。見
    docs/plans/2026-08-06-persist-user-selected-provider-design.md。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_llm_preferences (
            user_id     TEXT PRIMARY KEY,
            provider    TEXT NOT NULL,
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)


def create_user_custom_skills_table(c):
    """使用者自訂 skill（per-user，可新建/編輯/刪除）。

    自訂 skill 注入時用 <user_skill> 標籤包裝 + 隱形安全框架
    （後端組裝，API/前端永不暴露安全框架）。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_custom_skills (
            id               SERIAL PRIMARY KEY,
            user_id          TEXT NOT NULL,
            skill_name       TEXT NOT NULL,
            description      TEXT DEFAULT '',
            trigger_keywords TEXT DEFAULT '',
            body             TEXT NOT NULL,
            is_enabled       BOOLEAN DEFAULT TRUE,
            created_at       TIMESTAMP DEFAULT NOW(),
            updated_at       TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, skill_name)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_custom_skills_user "
        "ON user_custom_skills(user_id)"
    )


def create_notifications_table(c):
    """Create notifications table"""
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT,
            body TEXT,
            data JSONB,
            is_read BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),

            CONSTRAINT fk_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_user_unread ON notifications(user_id) WHERE is_read = FALSE"
    )


def create_experience_tables(c):
    """Create task_experiences and tool_execution_stats tables for Phase 3 memory."""
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_experiences (
            id              BIGSERIAL PRIMARY KEY,
            user_id         TEXT NOT NULL,
            session_id      TEXT NOT NULL,
            task_family     TEXT NOT NULL,
            query_text      TEXT NOT NULL,
            query_tsv       TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', query_text)) STORED,
            tools_used      TEXT[],
            agent_used      TEXT,
            outcome         TEXT NOT NULL,
            quality_score   REAL,
            failure_reason  TEXT,
            response_chars  INT,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_te_user_family ON task_experiences(user_id, task_family)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_te_created ON task_experiences(created_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_te_tsv ON task_experiences USING GIN(query_tsv)"
    )
    # learned_skills 表已於 2026-08-18 c029 移除（LearnedSkillStore 隨 c014
    # memory convergence 刪除後無任何讀寫——死表清理）


def create_knowledge_tables(c):
    """Create knowledge_pages table for LLM Wiki knowledge compounding (方向③).

    Karpathy LLM Wiki 模式:有價值的分析存成可重用的知識頁,下次類似問題
    agent 先查 wiki 而非從零合成。完全仿 task_experiences 的 FTS 模式
    (to_tsvector + GIN + ts_rank),不引入 vector DB。

    - body_tsv: 對 title+body 做 FTS(檢索用)
    - quality_score: 擷取啟發式分數 + 使用者 promote 可提升
    - access_count: 檢索時 touch(熱門知識浮上來)
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_pages (
            id              BIGSERIAL PRIMARY KEY,
            owner_user_id   TEXT NOT NULL,
            title           TEXT NOT NULL,
            body            TEXT NOT NULL,
            source_query    TEXT,
            source_session_id TEXT,
            tags            TEXT[],
            quality_score   REAL DEFAULT 0.0,
            access_count    INTEGER NOT NULL DEFAULT 0,
            promoted        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW(),
            body_tsv        TSVECTOR GENERATED ALWAYS AS (
                to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(body, ''))
            ) STORED
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_owner ON knowledge_pages(owner_user_id)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_promoted ON knowledge_pages(owner_user_id, promoted) "
        "WHERE promoted = TRUE"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_kp_tsv ON knowledge_pages USING GIN(body_tsv)"
    )


def create_trust_score_tables(c):
    """Create user_trust_scores table (trust_score feature, c015).

    Append-only history of every trust_score recomputation, with full signal
    breakdown JSON for audit/calibration. The latest row per user is the
    current score; ``users.trust_score`` is a denormalized cache.
    Idempotent — also covered by alembic c015.
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_trust_scores (
            id                  BIGSERIAL PRIMARY KEY,
            user_id             TEXT NOT NULL,
            trust_score         INTEGER NOT NULL DEFAULT 0,
            tier                TEXT NOT NULL DEFAULT 'anonymous',
            signal_breakdown    JSONB NOT NULL DEFAULT '{}'::jsonb,
            recompute_reason    TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_uts_user "
        "ON user_trust_scores(user_id, created_at DESC)"
    )


def reconcile_user_tables(c):
    """Safely reconcile legacy users schema without enforcing destructive changes."""
    messages = _run_reconcile_steps(
        c,
        "users",
        [
            (
                "role",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'user'",
            ),
            (
                "is_active",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
            ),
            (
                "current_session_id",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id TEXT",
            ),
            (
                "language",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT",
            ),
            # c039: 帳本報表基準幣（NULL＝TWD，向後相容）
            (
                "base_currency",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS base_currency TEXT",
            ),
            # c010: 個人化暱稱 + 24h 改名冷卻
            (
                "display_name",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT",
            ),
            (
                "display_name_updated_at",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "display_name_updated_at TIMESTAMPTZ",
            ),
            # c015: trust_score 快取欄位（徽章查詢免 join user_trust_scores）
            (
                "trust_score",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "trust_score INTEGER NOT NULL DEFAULT 0",
            ),
            (
                "trust_tier",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "trust_tier TEXT NOT NULL DEFAULT 'anonymous'",
            ),
            (
                "trust_score_updated_at",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "trust_score_updated_at TIMESTAMPTZ",
            ),
            # c016: EVM 地址綁定（Human Passport 訊號用）
            (
                "evm_address",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS evm_address TEXT",
            ),
            (
                "evm_bound_at",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS evm_bound_at TIMESTAMPTZ",
            ),
            # c018: 錢包監測警示設定（JSONB，v1 最小化）
            (
                "wallet_alert_settings",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet_alert_settings JSONB",
            ),
        ],
    )

    # c011: display_name 唯一索引自癒（alembic c011 已去重建好；這裡只補「還沒有」的情況）。
    # 2026-10-01 起不再自動去重：以前會把重複暱稱清成 NULL（改使用者資料），而且這段從沒真的
    # 在正式站跑過（見 _run_in_savepoint）。有重複就跳過、記 log，交給人處理
    c.execute(
        """
        SELECT 1 FROM users
        WHERE display_name IS NOT NULL
        GROUP BY display_name
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    if c.fetchone():
        logger.warning("users.display_name has duplicates; skip uq_users_display_name self-heal")
    else:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_display_name "
            "ON users(display_name)"
        )
        messages.append("uq_display_name")

    return messages


def reconcile_telegram_tables(c):
    """Ensure telegram_bindings has the active_session_id column on every startup.

    The /sessions switch feature stores the chosen session here. This makes the
    column self-healing regardless of alembic state (the prod schema is bootstrapped
    by init_db, and alembic has historically lagged), so the feature can never be
    silently blocked by an unapplied migration (mirrors alembic c005).
    """
    return _run_reconcile_steps(
        c,
        "telegram_bindings",
        [
            (
                "active_session_id",
                "ALTER TABLE telegram_bindings "
                "ADD COLUMN IF NOT EXISTS active_session_id TEXT",
            ),
        ],
    )


def reconcile_session_tables(c):
    """sessions.pin_order（c067）：AI 對話置頂的拖曳順序。舊 DB 的 sessions 是 CREATE TABLE IF NOT EXISTS
    no-op，欄位要在這裡補（同 alembic c067）。"""
    return _run_reconcile_steps(
        c,
        "sessions",
        [
            (
                "pin_order",
                "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pin_order INTEGER",
            ),
        ],
    )


def reconcile_group_chat_tables(c):
    """group_chats.dissolved_at（c068）：群主解散群組。舊 DB 的 group_chats 是 CREATE TABLE IF NOT EXISTS
    no-op，欄位要在這裡補（同 alembic c068）。"""
    return _run_reconcile_steps(
        c,
        "group_chats",
        [
            (
                "dissolved_at",
                "ALTER TABLE group_chats ADD COLUMN IF NOT EXISTS dissolved_at TIMESTAMPTZ",
            ),
        ],
    )


def reconcile_audit_log_tables(c):
    """Safely reconcile audit_logs columns and indexes for upgraded databases."""
    checked_items = _run_reconcile_steps(
        c,
        "audit_logs",
        [
            (
                "timestamp",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
            ),
            (
                "user_id",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_id VARCHAR(255)",
            ),
            (
                "username",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS username VARCHAR(255)",
            ),
            (
                "action",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS action VARCHAR(100)",
            ),
            (
                "resource_type",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS resource_type VARCHAR(100)",
            ),
            (
                "resource_id",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS resource_id VARCHAR(255)",
            ),
            (
                "endpoint",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS endpoint VARCHAR(255)",
            ),
            (
                "method",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS method VARCHAR(10)",
            ),
            (
                "ip_address",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(45)",
            ),
            (
                "user_agent",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_agent TEXT",
            ),
            (
                "request_data",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS request_data JSONB",
            ),
            (
                "response_code",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS response_code INTEGER",
            ),
            (
                "success",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS success BOOLEAN DEFAULT TRUE",
            ),
            (
                "error_message",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS error_message TEXT",
            ),
            (
                "duration_ms",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER",
            ),
            (
                "metadata",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS metadata JSONB",
            ),
            (
                "created_at",
                "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()",
            ),
            (
                "idx_user_id",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)",
            ),
            (
                "idx_timestamp",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs(timestamp DESC)",
            ),
            (
                "idx_action",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action)",
            ),
            (
                "idx_endpoint",
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_endpoint ON audit_logs(endpoint)",
            ),
        ],
    )

    c.execute(
        "UPDATE audit_logs SET endpoint = COALESCE(endpoint, 'system://legacy'), method = COALESCE(method, 'SYSTEM') "
        "WHERE endpoint IS NULL OR method IS NULL"
    )
    checked_items.extend(["endpoint_backfill", "method_backfill"])
    logger.info(
        "Schema reconcile checked audit_logs data defaults: endpoint_backfill, method_backfill"
    )
    return checked_items


def reconcile_scam_tracker_tables(c):
    """Safely reconcile legacy scam-tracker schema artifacts."""
    return _run_reconcile_steps(
        c,
        "scam_report_comments",
        [
            (
                "drop_attachment_url",
                "ALTER TABLE scam_report_comments DROP COLUMN IF EXISTS attachment_url",
            ),
        ],
    )


def reconcile_tool_tables(c):
    """Safely reconcile legacy tools catalog schema."""
    return _run_reconcile_steps(
        c,
        "tools_catalog",
        [
            (
                "daily_limit_plus",
                "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS daily_limit_plus INTEGER",
            ),
            (
                "key_provider",
                "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS key_provider TEXT",
            ),
            (
                "key_mode",
                "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS key_mode TEXT DEFAULT 'none'",
            ),
            (
                "risk_level",
                "ALTER TABLE tools_catalog ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'low'",
            ),
        ],
    )


def reconcile_user_api_key_tables(c):
    """Safely reconcile user_api_keys schema (BYOK tool keys)."""
    return _run_reconcile_steps(
        c,
        "user_api_keys",
        [
            (
                "key_kind",
                "ALTER TABLE user_api_keys ADD COLUMN IF NOT EXISTS key_kind TEXT NOT NULL DEFAULT 'llm'",
            ),
        ],
    )


def reconcile_payment_tables(c):
    """Safely add UNIQUE constraints on tx_hash columns for payment integrity."""
    checked = []
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_payments_tx_hash "
            "ON membership_payments(tx_hash)"
        )
        checked.append("membership_payments.tx_hash_unique")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("Failed to add UNIQUE on membership_payments.tx_hash: %s", e)
    # c041: 多鏈付款欄位（可空，向後相容）——記錄收款 rail/資產與美元錨定價
    for _col_sql in (
        "ALTER TABLE membership_payments ADD COLUMN IF NOT EXISTS chain TEXT",
        "ALTER TABLE membership_payments ADD COLUMN IF NOT EXISTS asset TEXT",
        "ALTER TABLE membership_payments ADD COLUMN IF NOT EXISTS "
        "fiat_amount_usd NUMERIC(18,4)",
    ):
        try:
            c.execute(_col_sql)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.warning("membership_payments reconcile failed (%s): %s", _col_sql, e)
    checked.append("membership_payments.multichain_columns")
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tips_tx_hash ON tips(tx_hash)")
        checked.append("tips.tx_hash_unique")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("Failed to add UNIQUE on tips.tx_hash: %s", e)
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_payment_tx_hash "
            "ON posts(payment_tx_hash) WHERE payment_tx_hash IS NOT NULL"
        )
        checked.append("posts.payment_tx_hash_unique_partial")
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.warning("Failed to add partial UNIQUE on posts.payment_tx_hash: %s", e)
    if checked:
        logger.info("Payment schema reconcile checked: %s", ", ".join(checked))
    return checked


def reconcile_check_constraints(c):
    """Add CHECK constraints for data integrity."""
    checked = []
    constraints = [
        (
            "membership_payments",
            "ck_amount_positive",
            "ALTER TABLE membership_payments ADD CONSTRAINT ck_amount_positive CHECK (amount > 0)",
        ),
        (
            "membership_payments",
            "ck_months_positive",
            "ALTER TABLE membership_payments ADD CONSTRAINT ck_months_positive CHECK (months > 0)",
        ),
        (
            "tips",
            "ck_amount_positive",
            "ALTER TABLE tips ADD CONSTRAINT ck_amount_positive CHECK (amount > 0)",
        ),
        (
            "price_alerts",
            "ck_target_positive",
            "ALTER TABLE price_alerts ADD CONSTRAINT ck_target_positive CHECK (target > 0)",
        ),
        (
            "friendships",
            "ck_status_valid",
            "ALTER TABLE friendships ADD CONSTRAINT ck_status_valid CHECK (status IN ('pending', 'accepted', 'rejected', 'blocked'))",
        ),
        (
            "forum_comments",
            "ck_type_valid",
            "ALTER TABLE forum_comments ADD CONSTRAINT ck_type_valid CHECK (type IN ('comment', 'push', 'boo'))",
        ),
        (
            "scam_reports",
            "ck_verification_status_valid",
            "ALTER TABLE scam_reports ADD CONSTRAINT ck_verification_status_valid CHECK (verification_status IN ('pending', 'verified', 'rejected', 'investigating'))",
        ),
        (
            "content_reports",
            "ck_review_status_valid",
            "ALTER TABLE content_reports ADD CONSTRAINT ck_review_status_valid CHECK (review_status IN ('pending', 'approved', 'rejected', 'escalated'))",
        ),
        (
            "report_review_votes",
            "ck_vote_type_valid",
            "ALTER TABLE report_review_votes ADD CONSTRAINT ck_vote_type_valid CHECK (vote_type IN ('approve', 'reject'))",
        ),
    ]
    for table, name, sql in constraints:
        try:
            c.execute(sql)
            checked.append(f"{table}.{name}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug("Constraint %s.%s already exists or error: %s", table, name, e)
    if checked:
        logger.info("Check constraints added: %s", ", ".join(checked))
    return checked


def reconcile_foreign_keys(c):
    """Add missing foreign key constraints for data integrity."""
    checked = []
    foreign_keys = [
        (
            "admin_broadcasts",
            "fk_admin_broadcasts_user",
            "ALTER TABLE admin_broadcasts ADD CONSTRAINT fk_admin_broadcasts_user FOREIGN KEY (admin_user_id) REFERENCES users(user_id)",
        ),
        (
            "user_violations",
            "fk_user_violations_user",
            "ALTER TABLE user_violations ADD CONSTRAINT fk_user_violations_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "user_violation_points",
            "fk_user_violation_points_user",
            "ALTER TABLE user_violation_points ADD CONSTRAINT fk_user_violation_points_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "audit_reputation",
            "fk_audit_reputation_user",
            "ALTER TABLE audit_reputation ADD CONSTRAINT fk_audit_reputation_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "user_activity_logs",
            "fk_user_activity_logs_user",
            "ALTER TABLE user_activity_logs ADD CONSTRAINT fk_user_activity_logs_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "conversation_history",
            "fk_conversation_history_user",
            "ALTER TABLE conversation_history ADD CONSTRAINT fk_conversation_history_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "sessions",
            "fk_sessions_user",
            "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
        (
            "user_tool_preferences",
            "fk_user_tool_preferences_user",
            "ALTER TABLE user_tool_preferences ADD CONSTRAINT fk_user_tool_preferences_user FOREIGN KEY (user_id) REFERENCES users(user_id)",
        ),
    ]
    for table, name, sql in foreign_keys:
        try:
            c.execute(sql)
            checked.append(f"{table}.{name}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug(
                "Foreign key %s.%s already exists or error: %s", table, name, e
            )
    if checked:
        logger.info("Foreign keys added: %s", ", ".join(checked))
    return checked


def reconcile_numeric_columns(c):
    """Migrate financial REAL columns to NUMERIC(18,4) for precision."""
    checked = []
    migrations = [
        (
            "membership_payments",
            "amount",
            "ALTER TABLE membership_payments ALTER COLUMN amount TYPE NUMERIC(18,4) USING amount::NUMERIC(18,4)",
        ),
        (
            "posts",
            "tips_total",
            "ALTER TABLE posts ALTER COLUMN tips_total TYPE NUMERIC(18,4) USING tips_total::NUMERIC(18,4)",
        ),
        (
            "tips",
            "amount",
            "ALTER TABLE tips ALTER COLUMN amount TYPE NUMERIC(18,4) USING amount::NUMERIC(18,4)",
        ),
        (
            "price_alerts",
            "target",
            "ALTER TABLE price_alerts ALTER COLUMN target TYPE NUMERIC(18,4) USING target::NUMERIC(18,4)",
        ),
    ]
    for table, col, sql in migrations:
        try:
            c.execute(sql)
            checked.append(f"{table}.{col}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug("Column %s.%s migration skipped: %s", table, col, e)
    return checked


def reconcile_user_feedback_tables(c):
    """Migrate legacy feedback tables to the canonical user_feedback name."""
    return _run_reconcile_steps(
        c,
        "user_feedback",
        [
            (
                "rename_legacy_massege",
                """
                DO $$
                BEGIN
                    IF to_regclass('"user-massege"') IS NOT NULL
                       AND to_regclass('user_feedback') IS NULL THEN
                        EXECUTE 'ALTER TABLE "user-massege" RENAME TO user_feedback';
                    END IF;
                END $$;
                """,
            ),
            (
                "rename_legacy_massage",
                """
                DO $$
                BEGIN
                    IF to_regclass('"user-massage"') IS NOT NULL
                       AND to_regclass('user_feedback') IS NULL THEN
                        EXECUTE 'ALTER TABLE "user-massage" RENAME TO user_feedback';
                    END IF;
                END $$;
                """,
            ),
            (
                "ensure_table",
                """
                CREATE TABLE IF NOT EXISTS user_feedback (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    message TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                )
                """,
            ),
            (
                "ensure_user_index",
                """
                CREATE INDEX IF NOT EXISTS idx_user_feedback_user_id
                ON user_feedback (user_id)
                """,
            ),
            (
                "ensure_created_at_index",
                """
                CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at
                ON user_feedback (created_at DESC)
                """,
            ),
        ],
    )


def reconcile_content_reports(c):
    """Add missing columns to content_reports for finalize_report."""
    return _run_reconcile_steps(
        c,
        "content_reports",
        [
            (
                "points_assigned",
                "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS points_assigned INTEGER DEFAULT 0",
            ),
            (
                "action_taken",
                "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS action_taken VARCHAR(50)",
            ),
            (
                "processed_by",
                "ALTER TABLE content_reports ADD COLUMN IF NOT EXISTS processed_by VARCHAR(255)",
            ),
        ],
    )


def reconcile_drop_dead_columns(c):
    """Drop unused columns from existing databases."""
    drops = [
        ("users", "password_hash"),
        ("users", "email"),
        ("users", "pi_uid"),
        ("users", "pi_username"),
        ("users", "pi_wallet_address"),
        ("user_memory_cache", "long_term_memory"),
        ("scam_reports", "blockchain_type"),
        ("scam_reports", "reporter_wallet_address"),
    ]
    checked = []
    for table, col in drops:
        try:
            c.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}")
            checked.append(f"{table}.{col}")
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug("DROP COLUMN %s.%s skipped: %s", table, col, e)
    if checked:
        logger.info("Dropped dead columns: %s", ", ".join(checked))
    return checked


def reconcile_drop_dead_tables(c):
    """Drop unused tables from existing databases."""
    drops = [
        "login_attempts",
        "tool_execution_stats",
        "password_reset_tokens",
        "predictions",
    ]
    checked = []
    for table in drops:
        try:
            c.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            checked.append(table)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.debug("DROP TABLE %s skipped: %s", table, e)
    if checked:
        logger.info("Dropped dead tables: %s", ", ".join(checked))
    return checked


def create_trade_journal_table(c):
    """統一帳本（c032+c033，投資＋支出＋收入×多幣種×自動匯率）。

    跨市場（crypto/台股/美股/港股/日股/外匯/商品）× 現貨/合約/融資 × 多/空。
    數量單位依市場（幣量/張/股/手/口），統一 NUMERIC(20,8)。
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_journal (
            id              SERIAL PRIMARY KEY,
            user_id         VARCHAR(255) NOT NULL,

            symbol          VARCHAR(30) NOT NULL,
            market          VARCHAR(15) NOT NULL,
            instrument_type VARCHAR(10) NOT NULL DEFAULT 'spot',
            direction       VARCHAR(5) NOT NULL DEFAULT 'long',
            side            VARCHAR(4) NOT NULL DEFAULT 'buy',  -- c034
            leverage        NUMERIC(6,2) NOT NULL DEFAULT 1,

            quantity        NUMERIC(20,8) NOT NULL,
            price           NUMERIC(20,8) NOT NULL,
            currency        VARCHAR(5) NOT NULL DEFAULT 'USD',
            fee             NUMERIC(20,8) DEFAULT 0,
            fee_currency    VARCHAR(5) DEFAULT '',

            traded_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source          VARCHAR(10) DEFAULT 'chat',
            note            TEXT DEFAULT '',

            -- c033 統一帳本擴充
            entry_type      VARCHAR(10) NOT NULL DEFAULT 'trade',
            category        VARCHAR(20) NOT NULL DEFAULT 'other',
            base_currency   VARCHAR(5) NOT NULL DEFAULT 'TWD',
            converted_amount NUMERIC(20,8) DEFAULT NULL,
            exchange_rate   NUMERIC(20,8) DEFAULT NULL,
            rate_source     VARCHAR(10) NOT NULL DEFAULT 'auto',

            deleted_at      TIMESTAMPTZ DEFAULT NULL,  -- c035 soft delete
            chain           VARCHAR(20) DEFAULT NULL,  -- c047 鏈上同步來源
            tx_hash         VARCHAR(90) DEFAULT NULL,  -- c047
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_user "
        "ON trade_journal(user_id, traded_at DESC)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_symbol "
        "ON trade_journal(user_id, symbol)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_category "
        "ON trade_journal(user_id, entry_type, category)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_type_date "
        "ON trade_journal(user_id, entry_type, traded_at DESC)"
    )
    # c033 自癒補欄位（既有 trade_journal 表）
    _run_reconcile_steps(
        c,
        "trade_journal",
        [
            ("entry_type",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "entry_type VARCHAR(10) NOT NULL DEFAULT 'trade'"),
            ("category",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "category VARCHAR(20) NOT NULL DEFAULT 'other'"),
            ("base_currency",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "base_currency VARCHAR(5) NOT NULL DEFAULT 'TWD'"),
            ("converted_amount",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "converted_amount NUMERIC(20,8) DEFAULT NULL"),
            ("exchange_rate",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "exchange_rate NUMERIC(20,8) DEFAULT NULL"),
            ("rate_source",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "rate_source VARCHAR(10) NOT NULL DEFAULT 'auto'"),
            ("side",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "side VARCHAR(4) NOT NULL DEFAULT 'buy'"),
            # c047 鏈上同步來源
            ("chain",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "chain VARCHAR(20) DEFAULT NULL"),
            ("tx_hash",
             "ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS "
             "tx_hash VARCHAR(90) DEFAULT NULL"),
        ],
    )
    # c047 冪等鍵：同一筆鏈上轉帳只記一次（含已刪除）
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_trade_journal_onchain "
        "ON trade_journal (user_id, chain, tx_hash, symbol, side) "
        "WHERE tx_hash IS NOT NULL"
    )
    # 預防性索引（2026-08-23 審計）：數據量小時先建，成長後不需回填
    # note 搜尋（ILIKE）用 pg_trgm GIN——沒有這個，search 是全表掃描
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_note_trgm "
        "ON trade_journal USING gin (note gin_trgm_ops)"
    )
    # user_facts supersedes 鏈查詢 + 過期清理
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_facts_supersedes "
        "ON user_facts(user_id, supersedes_id) WHERE supersedes_id IS NOT NULL"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_facts_valid_until "
        "ON user_facts(valid_until) WHERE valid_until IS NOT NULL"
    )
    # 回收筒查詢（c038）：list_deleted WHERE user_id + deleted_at 排序——
    # 既有索引都不涵蓋 deleted_at 述詞
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_trade_journal_deleted "
        "ON trade_journal(user_id, deleted_at DESC) WHERE deleted_at IS NOT NULL"
    )


def create_journal_revisions_table(c):
    """帳本版本史（c037，append-only 修訂快照＋diff）。

    每次條目 create/update/delete/restore 與主變更同 tx 寫入一筆；
    (entry_id, revision_no) UNIQUE 兜底併發編號。
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_revisions (
            id             BIGSERIAL PRIMARY KEY,
            entry_id       INTEGER NOT NULL REFERENCES trade_journal(id),
            user_id        VARCHAR(255) NOT NULL,
            revision_no    INTEGER NOT NULL,
            action         VARCHAR(10) NOT NULL,
            changed_fields JSONB DEFAULT NULL,
            snapshot       JSONB NOT NULL,
            source         VARCHAR(10) NOT NULL DEFAULT 'manual',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (entry_id, revision_no)
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_journal_revisions_entry "
        "ON journal_revisions(user_id, entry_id, revision_no DESC)"
    )


def create_agent_platform_tables(c):
    """Agent platform 表（c023；design §12／impl plan Part B、D3）。

    與 ORM（core/orm/models.py）與 Alembic（c023_agent_platform.py）三處同步：
    user_agent_presets / user_favorites。
    （user_preset_skill_bindings / user_preset_tool_preferences 兩張子表
    已於 c029 死表清理移除。）
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_presets (
            preset_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'single',
            agent_ids TEXT[] NOT NULL,
            analysis_mode TEXT NOT NULL DEFAULT 'quick',
            action_policy TEXT NOT NULL DEFAULT 'read_only',
            capability_overrides JSONB NOT NULL DEFAULT '{}',
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            config_version TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT ck_preset_mode_valid CHECK (mode IN ('single','auto','team')),
            CONSTRAINT ck_preset_analysis_mode_valid CHECK (analysis_mode IN ('quick','verified','research')),
            CONSTRAINT ck_preset_action_policy_valid CHECK (action_policy IN ('read_only','confirm_actions')),
            CONSTRAINT ck_preset_name_len CHECK (char_length(name) BETWEEN 1 AND 50)
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_agent_presets_user ON user_agent_presets(user_id)"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_agent_presets_default "
        "ON user_agent_presets(user_id) WHERE is_default"
    )
    # user_preset_skill_bindings / user_preset_tool_preferences 兩張子表已於
    # 2026-08-18 c029 移除（c023 建表後從未有任何讀寫——死表清理；
    # preset 主表 user_agent_presets 與 user_favorites 照常建立）

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_favorites (
            fav_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            item_id TEXT NOT NULL,
            source_url TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_user_favorite UNIQUE (user_id, item_type, item_id),
            CONSTRAINT ck_favorite_item_type_valid CHECK (item_type IN ('manifund_project','manifund_user','oc_collective'))
        )
    """)
    # 既有庫自癒：放寬 CHECK 加 oc_collective（alembic c028；冪等）
    c.execute(
        "ALTER TABLE user_favorites DROP CONSTRAINT IF EXISTS ck_favorite_item_type_valid"
    )
    c.execute(
        "ALTER TABLE user_favorites ADD CONSTRAINT ck_favorite_item_type_valid "
        "CHECK (item_type IN ('manifund_project','manifund_user','oc_collective'))"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_favorites_user ON user_favorites(user_id)"
    )

    # ── 提案工作台（design 2026-08-16；migration c024，self-heal 冪等）──
    c.execute("""
        CREATE TABLE IF NOT EXISTS proposal_drafts (
            draft_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            cause TEXT,
            target_usd NUMERIC,
            status TEXT NOT NULL DEFAULT 'draft',
            current_version_no INTEGER NOT NULL DEFAULT 1,
            references_json JSONB DEFAULT '[]',
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_user_draft_title UNIQUE (user_id, title),
            CONSTRAINT ck_draft_status_valid CHECK (status IN ('draft','exported','archived'))
        )
    """)
    # Discover→Studio 參考動線（design 2026-08-17 §Phase 2；alembic c028；冪等）
    c.execute(
        "ALTER TABLE proposal_drafts ADD COLUMN IF NOT EXISTS references_json JSONB DEFAULT '[]'"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposal_drafts_user ON proposal_drafts(user_id)"
    )
    c.execute("""
        CREATE TABLE IF NOT EXISTS draft_versions (
            version_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES proposal_drafts(draft_id) ON DELETE CASCADE,
            version_no INTEGER NOT NULL,
            content_md TEXT NOT NULL,
            change_summary TEXT,
            source TEXT NOT NULL,
            char_delta INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT uq_draft_version_no UNIQUE (draft_id, version_no),
            CONSTRAINT ck_draft_version_source_valid CHECK (source IN ('human','ai_applied','autosave'))
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_draft_versions_draft ON draft_versions(draft_id)"
    )
    # c025：軌跡分享 token（冪等補欄＋唯一索引）
    c.execute("ALTER TABLE proposal_drafts ADD COLUMN IF NOT EXISTS share_token TEXT")
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_proposal_drafts_share_token "
        "ON proposal_drafts(share_token) WHERE share_token IS NOT NULL"
    )

    # ── 教練對話稽核鏈（design 2026-08-16 A 方案；migration c026）──
    c.execute("""
        CREATE TABLE IF NOT EXISTS coach_exchanges (
            exchange_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES proposal_drafts(draft_id) ON DELETE CASCADE,
            question TEXT NOT NULL DEFAULT '',
            response_json JSONB NOT NULL,
            language TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_coach_exchanges_draft ON coach_exchanges(draft_id)"
    )
    c.execute("""
        CREATE TABLE IF NOT EXISTS suggestion_outcomes (
            outcome_id TEXT PRIMARY KEY,
            exchange_id TEXT NOT NULL REFERENCES coach_exchanges(exchange_id) ON DELETE CASCADE,
            quote TEXT NOT NULL,
            outcome TEXT NOT NULL,
            version_no INTEGER,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            CONSTRAINT ck_suggestion_outcome_valid CHECK (outcome IN ('adopted','dismissed'))
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_suggestion_outcomes_exchange "
        "ON suggestion_outcomes(exchange_id)"
    )

    # ── 輸入指紋＋前瞻評測（design 2026-08-17；migration c027）──
    for col in ("typed_chars", "paste_events", "pasted_chars", "edit_seconds"):
        c.execute(
            f"ALTER TABLE draft_versions ADD COLUMN IF NOT EXISTS {col} "
            "INTEGER NOT NULL DEFAULT 0"
        )
    c.execute("""
        CREATE TABLE IF NOT EXISTS prospect_logs (
            log_id TEXT PRIMARY KEY,
            project_slug TEXT NOT NULL,
            cause TEXT,
            stage_at_eval TEXT,
            content_snapshot JSONB NOT NULL,
            evaluation_json JSONB NOT NULL,
            language TEXT NOT NULL,
            model TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        )
    """)
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_prospect_logs_slug ON prospect_logs(project_slug)"
    )



# 啟動時自動跑的 reconcile：只放「加欄位、加索引、舊表改名」這類不會改壞資料的步驟。
#
# 刪表／刪欄位（reconcile_drop_dead_*、scam_report_comments.attachment_url）、改型別
# （timestamptz、numeric）、加 CHECK／FK 約束：2026-10-01 前因為交易中止從來沒在正式站跑過，
# 一旦修好 savepoint 就會在下次部署第一次真的執行（刪資料、改既有時間的解讀、讓之後的寫入被擋）。
# 這些改成要做時另寫 alembic migration，評估過再上（DANNY 2026-10-01 同意）。
SAFE_RECONCILE_STEPS = (
    ("payment_tables", lambda c: reconcile_payment_tables(c)),
    ("users", lambda c: reconcile_user_tables(c)),
    ("telegram_bindings", lambda c: reconcile_telegram_tables(c)),
    ("sessions", lambda c: reconcile_session_tables(c)),
    ("group_chats", lambda c: reconcile_group_chat_tables(c)),
    ("user_feedback", lambda c: reconcile_user_feedback_tables(c)),
    ("audit_logs", lambda c: reconcile_audit_log_tables(c)),
    ("tools_catalog", lambda c: reconcile_tool_tables(c)),
    ("user_api_keys", lambda c: reconcile_user_api_key_tables(c)),
    ("content_reports", lambda c: reconcile_content_reports(c)),
)


def reconcile_existing_tables(c):
    """Run safe (additive) schema reconciliation steps for existing databases."""
    return {label: _run_in_savepoint(c, label, fn) for label, fn in SAFE_RECONCILE_STEPS}


def format_reconcile_summary(summary):
    """Format reconcile results for startup logging."""
    non_empty = [f"{table}({len(items)})" for table, items in summary.items() if items]
    if not non_empty:
        return "no reconcile steps registered"
    return ", ".join(non_empty)


def create_user_agent_configs_table(c):
    """Model Mixer Step 1：user × agent per-agent 設定（c042）。

    與 ORM（core/orm/models.py:UserAgentConfig）與 Alembic
    （c042_user_agent_configs.py）三處同步。agent_id 不設 FK／CHECK：
    agent 目錄是檔案制（ProfileCatalog），由 API 層驗證。

    system_prompt（2026-09-09 per-agent prompt）：TEXT NULL，無列/NULL＝
    未設定；router 特例鍵的列只會有 model_selection。
    """
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_configs (
            preset_id TEXT NOT NULL REFERENCES user_agent_presets(preset_id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            model_selection JSONB,
            tools JSONB NOT NULL DEFAULT '[]',
            skills JSONB NOT NULL DEFAULT '[]',
            system_prompt TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            PRIMARY KEY (preset_id, agent_id)
        )
    """)
    # 舊庫補欄（既有列 system_prompt=NULL＝未設定，語意向下相容）
    c.execute(
        "ALTER TABLE user_agent_configs ADD COLUMN IF NOT EXISTS system_prompt TEXT"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_agent_configs_user "
        "ON user_agent_configs(user_id)"
    )


def create_daily_brief_tables(c):
    """每日早報偏好＋事件日曆（c045＋c046＋c052；docs/plans/2026-09-12-daily-brief-retention-design.md §6）。

    新 DB 走這裡建表；既有 DB 走 alembic c045／c046。兩邊 DDL 必須一致。
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_brief_prefs (
            user_id        TEXT PRIMARY KEY,
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            send_hour      SMALLINT NOT NULL DEFAULT 8,
            timezone       TEXT NOT NULL DEFAULT 'Asia/Taipei',
            channels       TEXT[] NOT NULL DEFAULT '{telegram,inapp}',
            include_spend  BOOLEAN NOT NULL DEFAULT TRUE,
            include_macro  BOOLEAN NOT NULL DEFAULT TRUE,
            last_sent_on   DATE,
            user_set       BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # c046 自癒補欄位（既有 user_brief_prefs 表）
    _run_reconcile_steps(
        c,
        "user_brief_prefs",
        [
            (
                "include_macro",
                "ALTER TABLE user_brief_prefs ADD COLUMN IF NOT EXISTS "
                "include_macro BOOLEAN NOT NULL DEFAULT TRUE",
            ),
            # c063：自己設的 vs 系統記 last_sent_on 自動建的（回填在 migration）
            (
                "user_set",
                "ALTER TABLE user_brief_prefs ADD COLUMN IF NOT EXISTS "
                "user_set BOOLEAN NOT NULL DEFAULT FALSE",
            ),
        ],
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_calendar_events (
            id                 BIGSERIAL PRIMARY KEY,
            user_id            TEXT NOT NULL,
            event_date         DATE NOT NULL,
            title              TEXT NOT NULL,
            kind               TEXT NOT NULL DEFAULT 'custom',
            symbol             TEXT,
            market             TEXT,
            source             TEXT NOT NULL DEFAULT 'user',
            remind_days_before SMALLINT NOT NULL DEFAULT 1,
            note               TEXT NOT NULL DEFAULT '',
            dedupe_key         TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            recurrence         TEXT NOT NULL DEFAULT 'none',
            recurrence_until   DATE,
            UNIQUE (user_id, dedupe_key)
        )
        """
    )
    # c052 自癒補欄位（既有 user_calendar_events 表）：重複事件
    _run_reconcile_steps(
        c,
        "user_calendar_events",
        [
            (
                "recurrence",
                "ALTER TABLE user_calendar_events ADD COLUMN IF NOT EXISTS "
                "recurrence TEXT NOT NULL DEFAULT 'none'",
            ),
            (
                "recurrence_until",
                "ALTER TABLE user_calendar_events ADD COLUMN IF NOT EXISTS "
                "recurrence_until DATE",
            ),
        ],
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_calendar_events_user_date "
        "ON user_calendar_events (user_id, event_date)"
    )


def create_onchain_sync_table(c):
    """綁定錢包鏈上同步狀態（c047；docs/plans/2026-09-12-wallet-ledger-calendar-integration.md §2）。

    新 DB 走這裡建表；既有 DB 走 alembic c047。兩邊 DDL 必須一致。
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_onchain_sync (
            user_id        TEXT PRIMARY KEY,
            enabled        BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMPTZ,
            last_result    JSONB,
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def create_judgment_scores_table(c):
    """判斷評分（c048；docs/plans/2026-09-13-judgment-scoring-design.md §5）。

    新 DB 走這裡建表；既有 DB 走 alembic c048。兩邊 DDL 必須一致。
    """
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS judgment_scores (
            id             BIGSERIAL PRIMARY KEY,
            user_id        TEXT NOT NULL,
            entry_id       BIGINT NOT NULL DEFAULT 0,
            call_id        BIGINT NOT NULL DEFAULT 0,
            kind           TEXT NOT NULL DEFAULT 'trade',
            symbol         TEXT NOT NULL,
            market         TEXT NOT NULL,
            side           TEXT NOT NULL,
            horizon_days   SMALLINT NOT NULL,
            entry_price    NUMERIC(20,8) NOT NULL,
            entry_date     DATE NOT NULL,
            exit_price     NUMERIC(20,8),
            exit_date      DATE NOT NULL,
            raw_pct        NUMERIC(10,4),
            bench_symbol   TEXT,
            bench_pct      NUMERIC(10,4),
            excess_pct     NUMERIC(10,4),
            hit            BOOLEAN,
            verification   TEXT NOT NULL DEFAULT 'self_reported',
            status         TEXT NOT NULL DEFAULT 'pending',
            attempts       SMALLINT NOT NULL DEFAULT 0,
            note           TEXT NOT NULL DEFAULT '',
            scored_at      TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, kind, entry_id, call_id, horizon_days)
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_scores_user "
        "ON judgment_scores (user_id, status, exit_date)"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_scores_due "
        "ON judgment_scores (status, exit_date)"
    )


def create_miniapp_notification_tokens_table(c):
    """Base App／Farcaster mini app 通知 token（c049）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS miniapp_notification_tokens (
            id              BIGSERIAL PRIMARY KEY,
            fid             BIGINT NOT NULL,
            app_fid         BIGINT,
            user_id         TEXT,
            url             TEXT NOT NULL,
            token           TEXT NOT NULL,
            enabled         BOOLEAN NOT NULL DEFAULT TRUE,
            disabled_reason TEXT,
            last_sent_at    TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (fid, url)
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_miniapp_notif_user "
        "ON miniapp_notification_tokens (user_id) WHERE enabled"
    )


def create_judgment_calls_table(c):
    """判斷評分 v2 明確喊單（c050）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS judgment_calls (
            id            BIGSERIAL PRIMARY KEY,
            user_id       TEXT NOT NULL,
            symbol        TEXT NOT NULL,
            market        TEXT NOT NULL,
            side          TEXT NOT NULL,
            horizon_days  SMALLINT NOT NULL,
            target_price  NUMERIC(20,8),
            entry_price   NUMERIC(20,8) NOT NULL,
            entry_date    DATE NOT NULL,
            note          TEXT NOT NULL DEFAULT '',
            source        TEXT NOT NULL DEFAULT 'chat',
            status        TEXT NOT NULL DEFAULT 'open',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            cancelled_at  TIMESTAMPTZ
        )
        """
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_judgment_calls_user "
        "ON judgment_calls (user_id, created_at DESC)"
    )


def create_google_bindings_table(c):
    """Google 帳號綁定（c051）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS google_bindings (
            google_sub    TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL UNIQUE,
            email         TEXT,
            display_name  TEXT,
            linked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at  TIMESTAMPTZ,
            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
        )
        """
    )


def create_user_email_subscriptions_table(c):
    """Email 早報訂閱（c053）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_email_subscriptions (
            user_id                 TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            email                   TEXT NOT NULL,
            confirm_token_hash      TEXT,
            confirm_sent_at         TIMESTAMPTZ,
            verified_at             TIMESTAMPTZ,
            unsubscribe_token_hash  TEXT,
            unsubscribed_at         TIMESTAMPTZ,
            created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sub_confirm_hash "
        "ON user_email_subscriptions (confirm_token_hash) "
        "WHERE confirm_token_hash IS NOT NULL"
    )
    c.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_sub_unsub_hash "
        "ON user_email_subscriptions (unsubscribe_token_hash) "
        "WHERE unsubscribe_token_hash IS NOT NULL"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_sub_email "
        "ON user_email_subscriptions (email)"
    )


def create_user_legal_acceptances_table(c):
    """服務條款／隱私政策同意紀錄（c054）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_legal_acceptances (
            user_id      TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            version      TEXT NOT NULL,
            accepted_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, version)
        )
        """
    )


def create_admin_setting_overrides_table(c):
    """後台覆寫的開關與額度（c055）。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_setting_overrides (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def create_user_watchlist_table(c):
    """使用者自選清單（c056）：市場＋代號。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_watchlist (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            market      TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, market, symbol)
        )
        """
    )


def create_user_brief_hidden_symbols_table(c):
    """早報不列的標的（c057）：市場＋代號。新 DB 走這裡，既有 DB 走 alembic；DDL 必須一致。"""
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_brief_hidden_symbols (
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            market      TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (user_id, market, symbol)
        )
        """
    )


def create_all_tables(c, conn=None):
    """Create all database tables.

    When ``conn`` is provided, each step is committed independently so that a
    failure in one table group cannot abort the whole transaction and silently
    skip every table that comes after it (psycopg2 aborts the transaction on the
    first error). Without ``conn`` the legacy all-or-nothing behaviour is kept
    and the first failure is re-raised.
    """
    steps = [
        ("basic", create_basic_tables),
        ("conversation", create_conversation_tables),
        ("user", create_user_tables),
        ("user_feedback", create_user_feedback_tables),
        ("notifications", create_notifications_table),
        ("experience", create_experience_tables),
        ("knowledge", create_knowledge_tables),
        ("trust_score", create_trust_score_tables),
        ("forum", create_forum_tables),
        ("scam_tracker", create_scam_tracker_tables),
        ("friendship", create_friendship_tables),
        ("dm", create_dm_tables),
        ("group_chat", create_group_chat_tables),
        ("chat_pins", create_chat_pins_table),
        ("chat_order", create_chat_order_table),
        ("chat_assistant_history", create_chat_assistant_history_table),
        ("shared_answers", create_shared_answers_table),
        ("audit_log", create_audit_log_tables),
        ("governance", create_governance_tables),
        ("analysis", create_analysis_tables),
        ("price_alert", create_price_alert_tables),
        ("tool", create_tool_tables),
        ("memory", create_memory_tables),
        ("user_facts", create_user_facts_table),
        ("user_facts_private", create_user_facts_private_table),
        ("trade_journal", create_trade_journal_table),
        ("journal_revisions", create_journal_revisions_table),
        ("user_skill_overrides", create_user_skill_overrides_table),
        ("user_custom_skills", create_user_custom_skills_table),
        ("user_llm_preferences", create_user_llm_preferences_table),
        ("agent_platform", create_agent_platform_tables),
        ("user_agent_configs", create_user_agent_configs_table),
        ("daily_brief", create_daily_brief_tables),
        ("onchain_sync", create_onchain_sync_table),
        ("judgment_scores", create_judgment_scores_table),
        ("miniapp_notification_tokens", create_miniapp_notification_tokens_table),
        ("judgment_calls", create_judgment_calls_table),
        ("google_bindings", create_google_bindings_table),
        ("user_email_subscriptions", create_user_email_subscriptions_table),
        ("user_legal_acceptances", create_user_legal_acceptances_table),
        ("admin_setting_overrides", create_admin_setting_overrides_table),
        ("user_watchlist", create_user_watchlist_table),
        ("user_brief_hidden_symbols", create_user_brief_hidden_symbols_table),
        ("indexes", create_indexes),
        ("default_data", init_default_data),
    ]

    failures = []
    for name, fn in steps:
        try:
            fn(c)
            if conn is not None:
                conn.commit()
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            if conn is not None:
                conn.rollback()
            failures.append((name, e))
            logger.error("Schema step '%s' failed: %s", name, e)

    if failures:
        if conn is None:
            # Legacy callers expect a hard failure.
            raise failures[0][1]
        logger.warning(
            "create_all_tables completed with %d failed step(s): %s",
            len(failures),
            ", ".join(name for name, _ in failures),
        )
    return failures
