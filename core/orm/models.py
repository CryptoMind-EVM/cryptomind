"""
SQLAlchemy 2.0 ORM declarative models.

These models mirror the existing psycopg2 schema in core/database/schema.py.
They are used for the async ORM migration and coexist with the raw SQL layer
during the transition period.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ?? Basic tables ????????????????????????????????????????????????????????????????


class Watchlist(Base):
    __tablename__ = "watchlist"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, primary_key=True)


class SystemCache(Base):
    __tablename__ = "system_cache"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class SystemConfig(Base):
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(Text, default="string")
    category: Mapped[str] = mapped_column(Text, default="general")
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_public: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ?? User tables ????????????????????????????????????????????????????????????????


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    auth_method: Mapped[str] = mapped_column(Text, default="ton_wallet")
    last_active_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    membership_tier: Mapped[str] = mapped_column(Text, default="free")
    membership_expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    role: Mapped[str] = mapped_column(Text, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    # c010: 個人化暱稱 + 24h 改名冷卻
    display_name: Mapped[Optional[str]] = mapped_column(Text)
    display_name_updated_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    # UI 語言偏好（DB 欄位早已存在於 core/database/schema.py，此處補上 ORM
    # mapping 供 async UserRepository.get_language 使用；欄位已存在故無需 migration）
    language: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("idx_users_last_active", "last_active_at"),
        Index("idx_users_membership", "membership_tier"),
        # c011: 暱稱唯一(論壇/社群場景避免混淆)。nullable + unique:
        # Postgres 允許多個 NULL,未設暱稱者不受影響。
        UniqueConstraint("display_name", name="uq_users_display_name"),
    )


class MembershipPayment(Base):
    __tablename__ = "membership_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    tx_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # c041: 多鏈付款欄位（可空，向後相容）——收款 rail/資產與美元錨定價
    chain: Mapped[Optional[str]] = mapped_column(Text)
    asset: Mapped[Optional[str]] = mapped_column(Text)
    fiat_amount_usd: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index("idx_membership_payments_user", "user_id"),
        CheckConstraint("amount > 0", name="ck_amount_positive"),
        CheckConstraint("months > 0", name="ck_months_positive"),
    )


class UserWallet(Base):
    """c041: 多鏈身份——帳號綁定的鏈上地址（multichain design Part A）。

    一個帳號可綁多個地址、一個地址只能屬於一個帳號（PK(chain, address)）。
    chain ∈ {"ton", "evm"}；address 存正規化形式（TON friendly / EVM lower-case hex）。
    """

    __tablename__ = "user_wallets"

    chain: Mapped[str] = mapped_column(Text, primary_key=True)
    address: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    bound_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index("idx_user_wallets_user", "user_id"),
    )


class PaymentOrder(Base):
    """c041: 多 rail 付款訂單生命週期（multichain design Part C）。

    order_token 為 HMAC 簽署（含 rail），onchain_ref 為鏈上定位依據
    （TON comment / EVM tx hash）。跨鏈同 hash 防重放靠 UNIQUE(chain, onchain_ref)。
    """

    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    rail: Mapped[str] = mapped_column(Text, nullable=False)
    chain: Mapped[Optional[str]] = mapped_column(Text)
    asset: Mapped[Optional[str]] = mapped_column(Text)
    fiat_amount_usd: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    quoted_amount: Mapped[Optional[float]] = mapped_column(Numeric(20, 9))
    rate: Mapped[Optional[float]] = mapped_column(Numeric(20, 10))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="pending", nullable=False)
    order_token: Mapped[str] = mapped_column(Text, nullable=False)
    onchain_ref: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("chain", "onchain_ref", name="uq_payment_orders_chain_ref"),
        Index("idx_payment_orders_user", "user_id"),
    )


class AdminBroadcast(Base):
    __tablename__ = "admin_broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, default="announcement")
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    admin: Mapped["User"] = relationship(foreign_keys=[admin_user_id])


class UserApiKey(Base):
    __tablename__ = "user_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    model_selection: Mapped[Optional[str]] = mapped_column(Text)
    key_kind: Mapped[str] = mapped_column(Text, nullable=False, default="llm")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_api_key_provider"),
        Index("idx_user_api_keys_user", "user_id"),
    )


class UserSavedModel(Base):
    """使用者在某 provider 下保存過的模型清單。

    金鑰仍是一 provider 一把（user_api_keys），「使用中」的模型仍是
    user_api_keys.model_selection；這張表只記「綁過哪些模型」，
    讓同 provider 綁第二個模型不再覆蓋第一個。
    """

    __tablename__ = "user_saved_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "model", name="uq_user_saved_model"),
        Index("idx_user_saved_models_user", "user_id"),
    )


# ?? Conversation tables ????????????????????????????????????????????????????????


class TelegramBinding(Base):
    """Links a Telegram user to an existing platform account.

    A Telegram user must first register on the web app (BYOK model) and
    generate a link token. After verifying the token via the bot, this
    binding record is created so the bot can resolve telegram_id -> user_id
    and use the user's own API key (BYOK) when invoking the LLM.
    """

    __tablename__ = "telegram_bindings"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    username: Mapped[Optional[str]] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    linked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    active_session_id: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_telegram_binding_user"),
        Index("idx_telegram_bindings_user", "user_id"),
    )


class ConversationHistory(Base):
    __tablename__ = "conversation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, default="default")
    user_id: Mapped[str] = mapped_column(Text, default="local_user")
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    # Python attr is metadata_ to avoid shadowing Base.metadata
    metadata_: Mapped[Optional[str]] = mapped_column("metadata", Text)

    __table_args__ = (
        Index(
            "idx_conversation_history_session_timestamp",
            "session_id",
            "timestamp",
        ),
    )


class Session(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text, default="local_user")
    title: Mapped[Optional[str]] = mapped_column(Text)
    is_pinned: Mapped[int] = mapped_column(Integer, default=0)
    # 置頂的拖曳順序（c067）：取消置頂設 NULL；舊的置頂列是 NULL，排在有編號的後面
    pin_order: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_updated_at", "updated_at"),
    )


# ?? Forum tables ???????????????????????????????????????????????????????????????


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    board_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("boards.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[Optional[str]] = mapped_column(Text)
    push_count: Mapped[int] = mapped_column(Integer, default=0)
    boo_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    tips_total: Mapped[float] = mapped_column(Numeric(18, 4), default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    payment_tx_hash: Mapped[Optional[str]] = mapped_column(Text)
    is_pinned: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    board: Mapped["Board"] = relationship()
    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index("idx_posts_board_id", "board_id"),
        Index("idx_posts_user_id", "user_id"),
        Index("idx_posts_created_at", "created_at"),
        Index("idx_posts_category", "category"),
        Index(
            "idx_posts_payment_tx_hash",
            "payment_tx_hash",
            unique=True,
            postgresql_where=text("payment_tx_hash IS NOT NULL"),
        ),
    )


class ForumComment(Base):
    __tablename__ = "forum_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("forum_comments.id")
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    is_hidden: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    post: Mapped["Post"] = relationship()
    user: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint(
            "type IN ('comment', 'push', 'boo')", name="ck_forum_comment_type"
        ),
        Index("idx_forum_comments_post_id", "post_id"),
        Index("idx_forum_comments_user_id", "user_id"),
    )


class Tip(Base):
    __tablename__ = "tips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id"), nullable=False
    )
    from_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    to_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=1)
    tx_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    post: Mapped["Post"] = relationship()
    from_user: Mapped["User"] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped["User"] = relationship(foreign_keys=[to_user_id])

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_amount_positive"),
        Index("idx_tips_post_id", "post_id"),
        Index("idx_tips_from_user", "from_user_id"),
        Index("idx_tips_to_user", "to_user_id"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("idx_tags_name", "name"),)


class PostTag(Base):
    __tablename__ = "post_tags"

    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("posts.id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id"), primary_key=True
    )


class UserDailyComment(Base):
    __tablename__ = "user_daily_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_daily_comments"),
        Index("idx_user_daily_comments_user_date", "user_id", "date"),
    )


class UserDailyPost(Base):
    __tablename__ = "user_daily_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    post_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped["User"] = relationship()

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_daily_posts"),)


# ?? Scam tracker tables ????????????????????????????????????????????????????????


class ScamReport(Base):
    __tablename__ = "scam_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scam_wallet_address: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    reporter_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    reporter_wallet_masked: Mapped[str] = mapped_column(Text, nullable=False)
    scam_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_hash: Mapped[Optional[str]] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(Text, default="pending")
    approve_count: Mapped[int] = mapped_column(Integer, default=0)
    reject_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reporter: Mapped["User"] = relationship(foreign_keys=[reporter_user_id])

    __table_args__ = (
        CheckConstraint(
            "verification_status IN "
            "('pending', 'verified', 'rejected', 'investigating')",
            name="ck_verification_status_valid",
        ),
        Index("idx_scam_wallet", "scam_wallet_address"),
        Index("idx_scam_type", "scam_type"),
        Index("idx_scam_status", "verification_status"),
        Index("idx_scam_created", "created_at"),
    )


class ScamReportVote(Base):
    __tablename__ = "scam_report_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scam_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    vote_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    report: Mapped["ScamReport"] = relationship()
    user: Mapped["User"] = relationship(foreign_keys=[user_id])

    __table_args__ = (
        UniqueConstraint("report_id", "user_id", name="uq_scam_vote"),
        Index("idx_vote_report", "report_id"),
        Index("idx_vote_user", "user_id"),
    )


class ScamReportComment(Base):
    __tablename__ = "scam_report_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scam_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_hash: Mapped[Optional[str]] = mapped_column(Text)
    is_hidden: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    report: Mapped["ScamReport"] = relationship()
    user: Mapped["User"] = relationship(foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_comment_report", "report_id"),
        Index("idx_comment_created", "created_at"),
    )


# ?? Friendship tables ??????????????????????????????????????????????????????????


class Friendship(Base):
    __tablename__ = "friendships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    friend_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # 封鎖列才用（c062）。一對人只能有一列（idx_friendships_ordered_pair）：
    # user_id＝先封鎖的人；mutual_block＝friend_id 也封鎖了 user_id；
    # restore_on_unblock＝封鎖前是好友，最後一個人解除封鎖時恢復好友（LINE 式）
    restore_on_unblock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    mutual_block: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    friend: Mapped["User"] = relationship(foreign_keys=[friend_id])

    __table_args__ = (
        UniqueConstraint("user_id", "friend_id", name="uq_friendship_pair"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'blocked')",
            name="ck_friendship_status",
        ),
        Index("idx_friendships_user_id", "user_id"),
        Index("idx_friendships_friend_id", "friend_id"),
        Index("idx_friendships_status", "status"),
    )


# ?? DM tables ??????????????????????????????????????????????????????????????????


class DmConversation(Base):
    __tablename__ = "dm_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user1_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    user2_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    last_message_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    user1_unread_count: Mapped[int] = mapped_column(Integer, default=0)
    user2_unread_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user1_id", "user2_id", name="uq_dm_conversation_pair"),
        Index("idx_dm_conversations_user1", "user1_id"),
        Index("idx_dm_conversations_user2", "user2_id"),
        Index("idx_dm_conversations_last_message", "last_message_at"),
    )


class DmMessage(Base):
    __tablename__ = "dm_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dm_conversations.id"), nullable=False
    )
    from_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    to_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(Text, default="text")
    # 回覆引用（c059）：原訊息被刪掉時變 NULL，引用區塊就不顯示
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("dm_messages.id", ondelete="SET NULL")
    )
    is_read: Mapped[int] = mapped_column(Integer, default=0)
    read_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_dm_messages_conversation", "conversation_id"),
        Index("idx_dm_messages_created", "created_at"),
        Index("idx_dm_messages_from_user", "from_user_id"),
        Index("idx_dm_messages_to_user", "to_user_id"),
    )


class DmMessageReaction(Base):
    """私訊表情回應（c060）：每人每則一個，DB 只存 key（core.dm_reactions.REACTION_KEYS）"""

    __tablename__ = "dm_message_reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dm_messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    reaction: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_dm_message_reaction"),
    )


class DmReport(Base):
    """私訊檢舉（c061）：snapshot 是檢舉當下被檢舉那則＋前 10 則（收回會清空原文，證據留在這）。
    只有管理員看得到——不走論壇 content_reports 的社群投票。"""

    __tablename__ = "dm_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    reported_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dm_conversations.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("dm_messages.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    # c065：內容檢查的風險分數（後台排序用；檢查服務不在＝None）
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_category: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("reporter_user_id", "message_id", name="uq_dm_report"),
        Index("idx_dm_reports_status", "status", "created_at"),
    )


class DmMessageDeletion(Base):
    __tablename__ = "dm_message_deletions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("dm_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_dm_message_deletion"),
    )


# ── 群組聊天（c066，設計 docs/plans/2026-10-01-group-chat-design.md）──────────────


class GroupChat(Base):
    """Pro 小群私聊。群主帳號被刪 owner_id 設 NULL（群照常聊天，不連帶刪掉其他人的群）。
    群主解散（c068）：dissolved_at 有值、owner_id 設 NULL、成員全部移出，訊息留著當檢舉證據。"""

    __tablename__ = "group_chats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="SET NULL")
    )
    # 新成員可看歷史紀錄：只影響之後才入群的人（入群時決定 first_visible_message_id）
    history_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_message_id: Mapped[Optional[int]] = mapped_column(Integer)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    dissolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 30", name="ck_group_chat_name"),
    )


class GroupMember(Base):
    """看得到 id > first_visible_message_id 的訊息；last_read_message_id 只增不減（算未讀與「已讀 N」）"""

    __tablename__ = "group_members"

    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_chats.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    first_visible_message_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_read_message_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (Index("idx_group_members_user", "user_id"),)


class GroupMessage(Base):
    """message_type：text／recalled（收回清空 content）／system（content 是事件碼，前端組字）"""

    __tablename__ = "group_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_chats.id", ondelete="CASCADE"), nullable=False
    )
    from_user_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="SET NULL")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="text", server_default=text("'text'")
    )
    reply_to_message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("group_messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "message_type IN ('text', 'recalled', 'system')", name="ck_group_message_type"
        ),
        Index("idx_group_messages_group", "group_id", "id"),
    )


class GroupMessageReaction(Base):
    """同 DmMessageReaction：每人每則一個，DB 只存 key（core.dm_reactions.REACTION_KEYS）"""

    __tablename__ = "group_message_reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    reaction: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_group_message_reaction"),
    )


class GroupInvite(Base):
    """同一人在同一群只能有一張 pending（部分唯一索引）；處理掉的可以再邀"""

    __tablename__ = "group_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_chats.id", ondelete="CASCADE"), nullable=False
    )
    inviter_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    invitee_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    responded_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'cancelled')",
            name="ck_group_invite_status",
        ),
        Index(
            "uq_group_invite_pending",
            "group_id",
            "invitee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_group_invites_inviter", "inviter_id", "created_at"),
    )


class GroupReport(Base):
    """群組檢舉：同 DmReport，snapshot 是檢舉當下被檢舉那則＋前 10 則（限檢舉者看得到的範圍）"""

    __tablename__ = "group_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reporter_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    reported_user_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="SET NULL")
    )
    group_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("group_chats.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("group_messages.id", ondelete="SET NULL")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text)
    snapshot: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_category: Mapped[Optional[str]] = mapped_column(Text)
    resolved_by: Mapped[Optional[str]] = mapped_column(Text)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("reporter_user_id", "message_id", name="uq_group_report"),
        Index("idx_group_reports_status", "status", "created_at"),
    )


class ChatPin(Base):
    """對話置頂（c067）：私訊與群組共用，target_id 依 kind 指 dm_conversations.id 或 group_chats.id
    （多型，不設 FK）。position 越小越上面；失效的（退群、私訊列表看不到）在每次置頂操作時清掉。"""

    __tablename__ = "chat_pins"

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('dm', 'group')", name="ck_chat_pin_kind"),
        Index("idx_chat_pins_user_position", "user_id", "position"),
    )


class ChatOrder(Base):
    """對話自訂順序（c068）：社群列表「自訂順序」模式的位置，私訊與群組共用一個序列（0..n-1）。
    target_id 同 ChatPin 是多型、不設 FK；置頂的照 chat_pins 排，這裡的位置只在沒置頂時用。"""

    __tablename__ = "chat_order"

    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("kind IN ('dm', 'group')", name="ck_chat_order_kind"),
        Index("idx_chat_order_user_position", "user_id", "position"),
    )


class ChatAssistantHistory(Base):
    """聊天室 AI 助理的問答紀錄（c070）：只有提問的人看得到，跨裝置接續。
    target_id 同 ChatPin 是多型、不設 FK（dm_conversations.id 或 group_chats.id）。
    source_ids＝這次回答讀過的訊息 id：其中任一則被收回，這列就作廢（chat_assistant_history_repo）。"""

    __tablename__ = "chat_assistant_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default=text("'{}'")
    )
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('dm', 'group')", name="ck_chat_assistant_history_kind"),
        Index("idx_chat_assistant_history_chat", "user_id", "kind", "target_id", "id"),
        Index(
            "idx_chat_assistant_history_sources",
            "source_ids",
            postgresql_using="gin",
        ),
    )


class SharedAnswer(Base):
    """AI 回答快照分享（c072）：使用者選的那一輪問答（已遮蔽錢包地址等），免登入唯讀公開頁。
    token 不存，只存 SHA-256（資料庫外洩也拿不到可用連結）；到期或撤銷後讀不到。
    整個功能由旗標 CONVERSATION_SHARE_ENABLED 控制（預設關）。見 core/answer_share.py。"""

    __tablename__ = "shared_answers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        Index("idx_shared_answers_user", "user_id", text("created_at DESC")),
        Index("idx_shared_answers_expires", "expires_at"),
    )


class UserMessageLimit(Base):
    __tablename__ = "user_message_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id"), nullable=False
    )
    date: Mapped[datetime] = mapped_column(Date, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    greeting_count: Mapped[int] = mapped_column(Integer, default=0)
    greeting_month: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship()

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_message_limit"),
        Index("idx_user_message_limits", "user_id", "date"),
    )


# ?? Notification ???????????????????????????????????????????????????????????????


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[Optional[dict]] = mapped_column(JSONB)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        Index("idx_notifications_user_created", "user_id", created_at.desc()),
        Index("idx_notifications_user_unread", "user_id"),
    )


# ?? Audit log ??????????????????????????????????????????????????????????????????


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    user_id: Mapped[Optional[str]] = mapped_column(Text)
    username: Mapped[Optional[str]] = mapped_column(Text)
    action: Mapped[Optional[str]] = mapped_column(Text)
    resource_type: Mapped[Optional[str]] = mapped_column(Text)
    resource_id: Mapped[Optional[str]] = mapped_column(Text)
    endpoint: Mapped[Optional[str]] = mapped_column(Text)
    method: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(Text)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    request_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    response_code: Mapped[Optional[int]] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    # Python attr is metadata_ to avoid shadowing Base.metadata
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_audit_logs_user_id", "user_id"),
        Index("idx_audit_logs_timestamp", "timestamp"),
        Index("idx_audit_logs_action", "action"),
        Index("idx_audit_logs_endpoint", "endpoint"),
    )


# ?? Governance tables ??????????????????????????????????????????????????????????


class ContentReport(Base):
    __tablename__ = "content_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reporter_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    report_type: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(Text, default="pending")
    violation_level: Mapped[Optional[str]] = mapped_column(Text)
    approve_count: Mapped[int] = mapped_column(Integer, default=0)
    reject_count: Mapped[int] = mapped_column(Integer, default=0)
    points_assigned: Mapped[int] = mapped_column(Integer, default=0)
    action_taken: Mapped[Optional[str]] = mapped_column(Text)
    processed_by: Mapped[Optional[str]] = mapped_column(Text)
    # c065：內容檢查的風險分數（只給後台排序；社群投票佇列不顯示）
    risk_score: Mapped[Optional[float]] = mapped_column(Float)
    risk_category: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected', 'escalated')",
            name="ck_review_status_valid",
        ),
        Index("idx_content_reports_status", "review_status"),
        Index("idx_content_reports_reporter", "reporter_user_id"),
        Index("idx_content_reports_content", "content_type", "content_id"),
        Index("idx_content_reports_created", "created_at"),
    )


class ReportReviewVote(Base):
    __tablename__ = "report_review_votes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_reports.id"), nullable=False
    )
    reviewer_user_id: Mapped[str] = mapped_column(Text, nullable=False)
    vote_type: Mapped[str] = mapped_column(Text, nullable=False)
    vote_weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    report: Mapped["ContentReport"] = relationship()

    __table_args__ = (
        UniqueConstraint("report_id", "reviewer_user_id", name="uq_report_review_vote"),
        CheckConstraint(
            "vote_type IN ('approve', 'reject')", name="ck_vote_type_valid"
        ),
        Index("idx_report_review_votes_report", "report_id"),
    )


class UserViolation(Base):
    __tablename__ = "user_violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    violation_level: Mapped[str] = mapped_column(Text, nullable=False)
    violation_type: Mapped[Optional[str]] = mapped_column(Text)
    points: Mapped[int] = mapped_column(Integer, default=0)
    source_type: Mapped[Optional[str]] = mapped_column(Text)
    source_id: Mapped[Optional[int]] = mapped_column(Integer)
    action_taken: Mapped[Optional[str]] = mapped_column(Text)
    suspended_until: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    processed_by: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("idx_user_violations_user", "user_id"),)


class UserViolationPoints(Base):
    __tablename__ = "user_violation_points"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    total_violations: Mapped[int] = mapped_column(Integer, default=0)
    last_violation_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    suspension_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditReputation(Base):
    __tablename__ = "audit_reputation"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    total_reviews: Mapped[int] = mapped_column(Integer, default=0)
    correct_votes: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_rate: Mapped[float] = mapped_column(Float, default=0.0)
    reputation_score: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserActivityLog(Base):
    __tablename__ = "user_activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    activity_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(Text)
    resource_id: Mapped[Optional[int]] = mapped_column(Integer)
    # Python attr is metadata_ to avoid shadowing Base.metadata
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(Text)
    user_agent: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_user_activity_logs_user", "user_id"),
        Index("idx_user_activity_logs_type", "activity_type"),
    )


# ?? Analysis tables ????????????????????????????????????????????????????????????


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[Optional[str]] = mapped_column(Text)
    symbol: Mapped[Optional[str]] = mapped_column(Text)
    interval: Mapped[str] = mapped_column(Text, default="1d")
    report_text: Mapped[Optional[str]] = mapped_column(Text)
    # Python attr is metadata_ to avoid shadowing Base.metadata
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_analysis_reports_session", "session_id"),
        Index("idx_analysis_reports_user", "user_id"),
    )


# ?? Price alerts ???????????????????????????????????????????????????????????????


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    repeat: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    triggered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship()

    __table_args__ = (
        CheckConstraint("target > 0", name="ck_target_positive"),
        Index("idx_price_alerts_user", "user_id"),
        Index("idx_price_alerts_active", "triggered"),
    )


# ?? Tool tables ????????????????????????????????????????????????????????????????


class ToolsCatalog(Base):
    __tablename__ = "tools_catalog"

    tool_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    tier_required: Mapped[str] = mapped_column(Text, default="free")
    quota_type: Mapped[str] = mapped_column(Text, default="unlimited")
    daily_limit_free: Mapped[Optional[int]] = mapped_column(Integer)
    daily_limit_plus: Mapped[Optional[int]] = mapped_column(Integer)
    daily_limit_prem: Mapped[Optional[int]] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(Text, default="native")
    key_provider: Mapped[Optional[str]] = mapped_column(Text)
    key_mode: Mapped[str] = mapped_column(Text, default="none")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_tools_catalog_category", "category"),
        Index("idx_tools_catalog_tier", "tier_required"),
    )


class AgentToolPermission(Base):
    __tablename__ = "agent_tool_permissions"

    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tool_id: Mapped[str] = mapped_column(Text, primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (Index("idx_agent_tool_agent", "agent_id"),)


class UserToolPreference(Base):
    __tablename__ = "user_tool_preferences"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tool_id: Mapped[str] = mapped_column(Text, primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("idx_user_tool_prefs_user", "user_id"),)


class UserAnalysisPreference(Base):
    __tablename__ = "user_analysis_preferences"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled_tools: Mapped[Optional[list[str]]] = mapped_column(
        ARRAY(Text), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserLLMPreference(Base):
    """使用者選的 LLM provider（per-user 單列，跨裝置帶回）。

    只記「整體選了哪個 provider」；per-provider 的「上次用的 model」由
    UserApiKey.model_selection 負責。見
    docs/plans/2026-08-06-persist-user-selected-provider-design.md。
    """

    __tablename__ = "user_llm_preferences"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )



class ToolUsageLog(Base):
    __tablename__ = "tool_usage_log"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tool_id: Mapped[str] = mapped_column(Text, primary_key=True)
    used_date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    call_count: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (Index("idx_tool_usage_user_date", "user_id", "used_date"),)


# ?? Memory tables ??????????????????????????????????????????????????????????????


class UserMemory(Base):
    __tablename__ = "user_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    memory_type: Mapped[str] = mapped_column(Text, default="long_term")
    content: Mapped[Optional[str]] = mapped_column(Text)
    # Mixer Step 4（c043）：agent_id NULL＝共享（系統）；scope shared|private。
    # 建欄不接線——Step 5 私有 blob 起用（比照 Step 1 tools 欄先例）。
    agent_id: Mapped[Optional[str]] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text, default="shared", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # c043 刻意保留舊 UNIQUE(user_id, session_id, memory_type)：它是
        # write_long_term／write_compact_state 的 ON CONFLICT arbiter（換成
        # expression index 會弄死 consolidation——review P0）。agent 維度唯一
        # 性手術留給 Step 5 寫 agent blob 時連同 arbiter 一起換。
        UniqueConstraint("user_id", "session_id", "memory_type", name="uq_user_memory"),
        Index("idx_user_memory_user", "user_id"),
        Index("idx_user_memory_session", "session_id"),
        Index("idx_user_memory_type", "memory_type"),
        Index("idx_user_memory_agent", "user_id", "agent_id"),
    )


class UserHistoryLog(Base):
    __tablename__ = "user_history_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    entry: Mapped[str] = mapped_column(Text, nullable=False)
    tools_used: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_user_history_user", "user_id"),
        Index("idx_user_history_session", "session_id"),
        Index("idx_user_history_created", "created_at"),
    )


# ── Action Guard / external Agent SaaS ─────────────────────────────────────


class GuardClient(Base):
    __tablename__ = "guard_clients"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    environment: Mapped[str] = mapped_column(Text, nullable=False, default="live")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('active', 'suspended')", name="ck_guard_client_status"),
        CheckConstraint("environment IN ('test', 'live')", name="ck_guard_client_environment"),
        Index("idx_guard_clients_owner", "owner_user_id"),
    )


class GuardApiKey(Base):
    __tablename__ = "guard_api_keys"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        Text, ForeignKey("guard_clients.id", ondelete="CASCADE"), nullable=False
    )
    key_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    revoked_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_guard_api_keys_client", "client_id"),)


class GuardPolicy(Base):
    __tablename__ = "guard_policies"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        Text, ForeignKey("guard_clients.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("client_id", "version", name="uq_guard_policy_version"),
        Index(
            "uq_guard_policy_active",
            "client_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class GuardDecisionRecord(Base):
    __tablename__ = "guard_decisions"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(
        Text, ForeignKey("guard_clients.id", ondelete="CASCADE"), nullable=False
    )
    external_action_id: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    subject_ref_hash: Mapped[str] = mapped_column(Text, nullable=False)
    action_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_id: Mapped[str] = mapped_column(
        Text, ForeignKey("guard_policies.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    approval_expires_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    outcome: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "client_id", "external_action_id", name="uq_guard_external_action"
        ),
        UniqueConstraint(
            "client_id", "idempotency_key", name="uq_guard_idempotency_key"
        ),
        CheckConstraint(
            "decision IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL')",
            name="ck_guard_decision_value",
        ),
        CheckConstraint(
            "state IN ('allowed', 'denied', 'pending_approval', 'approved', 'executed', 'failed', 'expired')",
            name="ck_guard_decision_state",
        ),
        Index("idx_guard_decisions_client_created", "client_id", created_at.desc()),
    )


class ActionReceipt(Base):
    __tablename__ = "action_receipts"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("guard_clients.id")
    )
    user_id: Mapped[Optional[str]] = mapped_column(Text)
    decision_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("guard_decisions.id")
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    receipt_hash: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_action_receipts_client_created", "client_id", created_at.desc()),
        Index("idx_action_receipts_user_created", "user_id", created_at.desc()),
    )


class UserMemoryCache(Base):
    __tablename__ = "user_memory_cache"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    last_consolidated_index: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserFact(Base):
    __tablename__ = "user_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, default="high")
    source_turn: Mapped[Optional[int]] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_fact"),
        Index("idx_user_facts_user", "user_id"),
    )


class UserFactPrivate(Base):
    """Mixer Step 4（c043）：agent 私有事實層。

    共享表 ``user_facts`` 刻意不動——私有層實體分離，隔離邊界（哪張表可被
    agent 視角寫入）可直接審計；rollback＝DROP TABLE，免 constraint 手術。
    ``agent_id`` 為 ProfileCatalog profile id（app 層驗證，不設 FK——同
    user_agent_configs 先例）。
    """

    __tablename__ = "user_facts_private"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, default="high")
    source_turn: Mapped[Optional[int]] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(Text, default="fact")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "agent_id", "key", name="uq_user_fact_private"),
        Index("idx_user_fact_private_agent", "user_id", "agent_id"),
    )


# ?? Experience tables ??????????????????????????????????????????????????????????


class TaskExperience(Base):
    __tablename__ = "task_experiences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_family: Mapped[str] = mapped_column(Text, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    tools_used: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text))
    agent_used: Mapped[Optional[str]] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[Optional[float]] = mapped_column(Float)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    response_chars: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    # Generated column ??persisted TSVECTOR for full-text search
    query_tsv = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', query_text)", persisted=True),
    )

    __table_args__ = (
        Index("idx_te_user_family", "user_id", "task_family"),
        Index("idx_te_created", "created_at"),
        Index("idx_te_tsv", "query_tsv", postgresql_using="gin"),
    )


# ── Agent platform tables（c023；design §12／impl plan Part B、D3）──────────────


class UserAgentPreset(Base):
    """使用者的 Agent Preset（最多 10 個／人，上限由 repo/API 層強制）。

    capability_overrides 只存 capability 開關（JSONB），不存任意 prompt。
    config_version 為建立當下的官方 catalog 版本快照。
    """

    __tablename__ = "user_agent_presets"

    preset_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, default="single", nullable=False)
    agent_ids: Mapped[list] = mapped_column(ARRAY(Text), nullable=False)
    analysis_mode: Mapped[str] = mapped_column(Text, default="quick", nullable=False)
    action_policy: Mapped[str] = mapped_column(
        Text, default="read_only", nullable=False
    )
    capability_overrides: Mapped[Optional[dict]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_user_agent_presets_user", "user_id"),
        CheckConstraint(
            "mode IN ('single','auto','team')", name="ck_preset_mode_valid"
        ),
        CheckConstraint(
            "analysis_mode IN ('quick','verified','research')",
            name="ck_preset_analysis_mode_valid",
        ),
        CheckConstraint(
            "action_policy IN ('read_only','confirm_actions')",
            name="ck_preset_action_policy_valid",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 50", name="ck_preset_name_len"
        ),
        # 每使用者最多一個 default（partial unique index；NULL 不受限）
        Index(
            "uq_user_agent_presets_default",
            "user_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class UserAgentConfig(Base):
    """Model Mixer Step 1：preset × agent 的 per-agent 設定（user_agent_configs）。

    c044 起主鍵是 ``(preset_id, agent_id)``。在那之前是 ``(user_id, agent_id)``，
    也就是所有 preset 共用一份設定——改一個 preset 的工具，另一個跟著變，而
    UI 上看不出來。

    agent_id 刻意不設 FK／CHECK：agent 目錄是檔案制（ProfileCatalog），DB 硬編
    清單會讓每個新 agent 都要多一次遷移；合法性由 API 層驗證。
    model_selection NULL（或無列）= 官方預設；tools 為 per-agent 工具勾選
    白名單（Step 2 起已接線，空 list = 未設定）；skills 為 Step 4（記憶分層）
    預留，尚未接線。
    """

    __tablename__ = "user_agent_configs"

    # c044：主鍵是 (preset_id, agent_id)——per-agent 設定**只存在於 preset**。
    # 沒有使用者層、沒有 fallback：preset 裡看到什麼，跑的就是什麼。
    # user_id 保留供查詢與稽核，但不再是鍵的一部分。
    preset_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("user_agent_presets.preset_id", ondelete="CASCADE"),
        primary_key=True,
    )
    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    model_selection: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # SQL 端是 NOT NULL DEFAULT '[]'（c042／schema.py）；ORM 必須跟著標
    # nullable=False，否則 Step 2 寫 tools=None（比照 model_selection=None 的
    # 「重設」語義）會在 flush 才炸 NotNullViolation，型別標註完全擋不到。
    tools: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    skills: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Per-agent 自訂 system prompt（2026-09-09）：NULL＝未設定；寫入端過
    # sanitize_system_prompt，注入端 append-only（不可覆蓋官方指示）。
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserFavorite(Base):
    """探索介面收藏（Priority 1 輕量標記；impl plan Task D3、決策 12：Pilot 不限額）。"""

    __tablename__ = "user_favorites"

    fav_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(Text, nullable=False)
    item_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "item_type", "item_id", name="uq_user_favorite"),
        Index("idx_user_favorites_user", "user_id"),
        CheckConstraint(
            "item_type IN ('manifund_project','manifund_user','oc_collective')",
            name="ck_favorite_item_type_valid",
        ),
    )


class ProposalDraft(Base):
    """提案工作台草稿主檔（design 2026-08-16 §資料模型；金流不過手）。"""

    __tablename__ = "proposal_drafts"

    draft_id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        Text, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    cause: Mapped[Optional[str]] = mapped_column(Text)
    target_usd: Mapped[Optional[float]] = mapped_column(Numeric)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Discover→Studio 參考動線（design 2026-08-17 §Phase 2）：
    # 使用者從 Discover 卡片帶入的參考專案（source/url/title/snippet；上限 20 筆）
    references_json: Mapped[Optional[dict]] = mapped_column(JSONB, server_default="[]")
    # 軌跡分享（design 硬邊界 #5：預設私有；分享=opt-in，token 即能力，可撤銷）
    share_token: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "title", name="uq_user_draft_title"),
        Index("idx_proposal_drafts_user", "user_id"),
        CheckConstraint(
            "status IN ('draft','exported','archived')",
            name="ck_draft_status_valid",
        ),
    )


class CoachExchange(Base):
    """教練對話存檔（不可變；design 2026-08-16 A 方案——採納掛鏈的稽核源頭）。

    使用者每次請教練看稿的完整往返：問題＋教練回應全文（過濾後）。
    採納/忽略以 suggestion_outcomes 掛回本表，構成可交叉驗證的鏈路：
    「這句採納的文字確實存在於事前的 AI 回應，且進稿時間在後」。
    """

    __tablename__ = "coach_exchanges"

    exchange_id: Mapped[str] = mapped_column(Text, primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        Text, ForeignKey("proposal_drafts.draft_id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_coach_exchanges_draft", "draft_id"),
    )


class SuggestionOutcome(Base):
    """建議處置記錄（adopted 掛 version_no；dismissed 無版本）。"""

    __tablename__ = "suggestion_outcomes"

    outcome_id: Mapped[str] = mapped_column(Text, primary_key=True)
    exchange_id: Mapped[str] = mapped_column(
        Text, ForeignKey("coach_exchanges.exchange_id", ondelete="CASCADE"), nullable=False
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    version_no: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_suggestion_outcomes_exchange", "exchange_id"),
        CheckConstraint(
            "outcome IN ('adopted','dismissed')",
            name="ck_suggestion_outcome_valid",
        ),
    )


class DraftVersion(Base):
    """草稿版本（不可變：只插入、不更新不刪除；design 硬邊界 #6）。"""

    __tablename__ = "draft_versions"

    version_id: Mapped[str] = mapped_column(Text, primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        Text, ForeignKey("proposal_drafts.draft_id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    char_delta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 輸入指紋（design 2026-08-17 §2——人類側可驗證性）
    typed_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paste_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pasted_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    edit_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("draft_id", "version_no", name="uq_draft_version_no"),
        Index("idx_draft_versions_draft", "draft_id"),
        CheckConstraint(
            "source IN ('human','ai_applied','autosave')",
            name="ck_draft_version_source_valid",
        ),
    )


class ProspectLog(Base):
    """前瞻評測記錄（design 2026-08-17 §3——ai-review 成功即寫）。

    每筆＝一次帶時間戳的事前評估（快照＋評估＋模型），事後可對照實際
    募資結果；memorization 免疫（預測存於結果揭曉前）。
    """

    __tablename__ = "prospect_logs"

    log_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_slug: Mapped[str] = mapped_column(Text, nullable=False)
    cause: Mapped[Optional[str]] = mapped_column(Text)
    stage_at_eval: Mapped[Optional[str]] = mapped_column(Text)
    content_snapshot: Mapped[dict] = mapped_column(JSONB)
    evaluation_json: Mapped[dict] = mapped_column(JSONB)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("idx_prospect_logs_slug", "project_slug"),
    )


class ChatAttachment(Base):
    """對話附圖（vision Phase 2，c040）——壓縮圖 bytea＋30 天保留＋隨對話刪除。"""

    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_chat_attachments_created_at", "created_at"),
        Index("idx_chat_attachments_user_id", "user_id"),
    )
