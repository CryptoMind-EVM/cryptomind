"""
Async ORM repository for ScamTracker operations.

Provides async equivalents of the functions in core.database.scam_tracker,
using SQLAlchemy 2.0 select/update with ScamReport, ScamReportVote, and
ScamReportComment models.

Usage::

    from core.orm.scam_tracker_repo import scam_tracker_repo

    result = await scam_tracker_repo.create_report(...)
    reports = await scam_tracker_repo.get_reports()
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from core.onchain.addresses import scam_address_lookup_keys
from core.validators import mask_wallet_address

from .models import ScamReport, ScamReportComment, ScamReportVote
from .session import using_session

logger = logging.getLogger(__name__)


def _public_reporter_wallet(value: str | None) -> str | None:
    """reporter_wallet_masked 對外一律遮罩（公開的列表／搜尋／詳情都會回這欄）。

    ORM 建立路徑以前把完整地址直接寫進這欄（legacy psycopg2 版有遮、ORM 版漏了）。
    舊列不改 DB，輸出時補遮；已遮過（含 ... 或 …）或短於真實地址（舉報 API 收
    32 字元起）就原樣回，重複遮也不會變。
    """
    v = value or ""
    if "..." in v or "…" in v or len(v) < 32:
        return value
    return mask_wallet_address(v)


def _fmt(dt: datetime | None) -> str | None:
    """Format a datetime to ISO string, matching legacy psycopg2 output."""
    if dt is None:
        return None
    return dt.isoformat()


def _report_row_to_dict(r) -> dict:
    """Convert a ScamReport row (from a select with User join) to dict."""
    # Column order from get_scam_reports query:
    # id, scam_wallet_address, scam_type, description,
    # verification_status, approve_count, reject_count,
    # comment_count, view_count, reporter_wallet_masked,
    # created_at, reporter_username
    created_at = _fmt(r.created_at)
    desc = r.description
    if len(desc) > 200:
        desc = desc[:200] + "..."

    username = None
    if hasattr(r, "reporter") and r.reporter:
        username = r.reporter.username

    return {
        "id": r.id,
        "scam_wallet_address": r.scam_wallet_address,
        "scam_type": r.scam_type,
        "description": desc,
        "verification_status": r.verification_status,
        "approve_count": r.approve_count,
        "reject_count": r.reject_count,
        "comment_count": r.comment_count,
        "view_count": r.view_count,
        "reporter_wallet_masked": _public_reporter_wallet(r.reporter_wallet_masked),
        "created_at": created_at,
        "reporter_username": username,
        "net_votes": r.approve_count - r.reject_count,
    }


def _report_detail_to_dict(r, viewer_vote: Optional[str] = None) -> dict:
    """Convert a ScamReport row to full detail dict."""
    username = None
    if hasattr(r, "reporter") and r.reporter:
        username = r.reporter.username

    return {
        "id": r.id,
        "scam_wallet_address": r.scam_wallet_address,
        "scam_type": r.scam_type,
        "description": r.description,
        "transaction_hash": r.transaction_hash,
        "verification_status": r.verification_status,
        "approve_count": r.approve_count,
        "reject_count": r.reject_count,
        "comment_count": r.comment_count,
        "view_count": r.view_count,
        "reporter_wallet_masked": _public_reporter_wallet(r.reporter_wallet_masked),
        "created_at": _fmt(r.created_at),
        "updated_at": _fmt(r.updated_at),
        "reporter_username": username,
        "net_votes": r.approve_count - r.reject_count,
        "viewer_vote": viewer_vote,
    }


def _comment_row_to_dict(c) -> dict:
    """Convert a ScamReportComment row (with User join) to dict."""
    username = None
    if hasattr(c, "user") and c.user:
        username = c.user.username

    return {
        "id": c.id,
        "content": c.content,
        "transaction_hash": c.transaction_hash,
        "created_at": _fmt(c.created_at),
        "username": username,
    }


class ScamTrackerRepository:
    # ── Report CRUD ───────────────────────────────────────────────────────────

    async def create_report(
        self,
        scam_wallet_address: str,
        reporter_user_id: str,
        reporter_wallet_masked: str,
        scam_type: str,
        description: str,
        transaction_hash: Optional[str] = None,
        verification_status: str = "pending",
        session: AsyncSession | None = None,
    ) -> Dict:
        """
        Create a new scam report.

        Note: Validation (premium check, daily limit, content filtering,
        wallet masking) is the caller's job, mirroring the legacy
        ``create_scam_report`` — see ``create_new_scam_report`` in
        api/routers/scam_tracker/reports.py. Dedup is done here.

        Returns:
            {"success": True, "report_id": int}
            or {"success": False, "error": "already_reported", "existing_report_id": int}
        """
        # TON 存 raw、其他鏈照舊 upper()；去重要連舊的 upper() 寫法一起比
        keys = scam_address_lookup_keys(scam_wallet_address)

        report = ScamReport(
            scam_wallet_address=keys[0],
            reporter_user_id=reporter_user_id,
            reporter_wallet_masked=reporter_wallet_masked,
            scam_type=scam_type,
            description=description,
            transaction_hash=transaction_hash,
            verification_status=verification_status,
        )

        async with using_session(session) as s:
            existing_id = await self._first_report_id(s, keys)
            if existing_id is None:
                try:
                    async with s.begin_nested():
                        s.add(report)
                        await s.flush()
                except IntegrityError:
                    # 併發：同一個地址剛被另一筆搶先寫入（unique）
                    existing_id = await self._first_report_id(s, keys)
                    if existing_id is None:
                        raise
            if existing_id is not None:
                return {
                    "success": False,
                    "error": "already_reported",
                    "existing_report_id": existing_id,
                }
            await s.refresh(report)
            logger.info("Scam report created: %s by %s", report.id, reporter_user_id)
            return {"success": True, "report_id": report.id}

    async def count_reports_today(
        self, reporter_user_id: str, session: AsyncSession | None = None
    ) -> int:
        """這個人今天（UTC）已送出的舉報數——每日上限用（legacy 用 UTC 日期比）。"""
        today_start = datetime.now(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(func.count(ScamReport.id)).where(
            ScamReport.reporter_user_id == reporter_user_id,
            ScamReport.created_at >= today_start,
        )
        async with using_session(session) as s:
            return (await s.execute(stmt)).scalar() or 0

    @staticmethod
    async def _first_report_id(s: AsyncSession, keys: List[str]) -> Optional[int]:
        stmt = (
            select(ScamReport.id)
            .where(ScamReport.scam_wallet_address.in_(keys))
            .order_by(ScamReport.id)
            .limit(1)
        )
        return (await s.execute(stmt)).scalar_one_or_none()

    async def get_reports(
        self,
        scam_type: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: str = "latest",
        limit: int = 20,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> List[Dict]:
        """
        Get a paginated list of scam reports with optional filters.

        Args:
            scam_type: Filter by scam type.
            status: Filter by verification_status (pending/verified/disputed).
            sort_by: "latest" | "most_voted" | "most_viewed".
            limit: Page size.
            offset: Offset for pagination.

        Returns:
            List of report dicts with truncated descriptions.
        """
        stmt = select(ScamReport).options(joinedload(ScamReport.reporter))

        if scam_type:
            stmt = stmt.where(ScamReport.scam_type == scam_type)
        if status:
            stmt = stmt.where(ScamReport.verification_status == status)

        # Sorting
        if sort_by == "most_voted":
            stmt = stmt.order_by(
                (ScamReport.approve_count - ScamReport.reject_count).desc(),
                ScamReport.created_at.desc(),
            )
        elif sort_by == "most_viewed":
            stmt = stmt.order_by(
                ScamReport.view_count.desc(),
                ScamReport.created_at.desc(),
            )
        else:
            stmt = stmt.order_by(ScamReport.created_at.desc())

        stmt = stmt.limit(limit).offset(offset)

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.scalars().unique().all()
            return [_report_row_to_dict(r) for r in rows]

    async def get_report_by_id(
        self,
        report_id: int,
        increment_view: bool = True,
        viewer_user_id: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> Optional[Dict]:
        """
        Get scam report details by ID.

        Args:
            report_id: Report ID.
            increment_view: Whether to increment view count.
            viewer_user_id: Viewer user ID (to look up their vote).

        Returns:
            Report detail dict or None.
        """
        async with using_session(session) as s:
            # Increment view count
            if increment_view:
                await s.execute(
                    update(ScamReport)
                    .where(ScamReport.id == report_id)
                    .values(
                        view_count=ScamReport.view_count + 1,
                        updated_at=datetime.now(UTC),
                    )
                )
                await s.flush()

            # Fetch report with reporter relationship
            stmt = (
                select(ScamReport)
                .options(joinedload(ScamReport.reporter))
                .where(ScamReport.id == report_id)
            )
            result = await s.execute(stmt)
            report = result.scalars().unique().first()

            if report is None:
                return None

            # Query viewer vote
            viewer_vote = None
            if viewer_user_id:
                vote_stmt = select(ScamReportVote.vote_type).where(
                    ScamReportVote.report_id == report_id,
                    ScamReportVote.user_id == viewer_user_id,
                )
                vote_result = await s.execute(vote_stmt)
                viewer_vote = vote_result.scalar_one_or_none()

            return _report_detail_to_dict(report, viewer_vote)

    async def find_reports_by_address(
        self,
        wallet_address: str,
        session: AsyncSession | None = None,
    ) -> List[Dict]:
        """
        All reports filed against a wallet, oldest first.

        TON 任何寫法（EQ／UQ／raw）都對得到 raw 列與舊的 upper() 列；同一個錢包
        可能有多列（舊資料 EQ、UQ 各報一次，或修復時撞 unique 留下的舊列），全部回傳。
        """
        stmt = (
            select(ScamReport)
            .options(joinedload(ScamReport.reporter))
            .where(
                ScamReport.scam_wallet_address.in_(
                    scam_address_lookup_keys(wallet_address)
                )
            )
            .order_by(ScamReport.id)
        )
        async with using_session(session) as s:
            result = await s.execute(stmt)
            return [_report_detail_to_dict(r) for r in result.scalars().unique().all()]

    async def search_wallet(
        self,
        wallet_address: str,
        session: AsyncSession | None = None,
    ) -> Optional[Dict]:
        """
        Search for a wallet address that has been reported.

        Returns:
            The oldest matching report detail dict, or None.
        """
        reports = await self.find_reports_by_address(wallet_address, session=session)
        return reports[0] if reports else None

    async def heal_legacy_address(
        self,
        report_id: int,
        legacy_address: str,
        raw_address: str,
        session: AsyncSession | None = None,
    ) -> str:
        """把舊的 upper() TON 地址改寫成 raw。回 "healed"／"skipped"／"conflict"。

        - WHERE 同時比對舊值：這列已被別的請求修好或改過就不動（skipped）。
        - 撞 unique（raw 列已存在）→ conflict：不刪不併，舊列原樣留著，查詢兩列都會回。
        - updated_at 不動：這是資料維護，不是舉報內容有更新。
        """
        stmt = (
            update(ScamReport)
            .where(
                ScamReport.id == report_id,
                ScamReport.scam_wallet_address == legacy_address,
            )
            .values(
                scam_wallet_address=raw_address,
                updated_at=ScamReport.updated_at,
            )
        )
        async with using_session(session) as s:
            try:
                async with s.begin_nested():
                    result = await s.execute(stmt)
            except IntegrityError:
                logger.info(
                    "Scam report %s legacy address kept (raw row already exists)",
                    report_id,
                )
                return "conflict"
            return "healed" if result.rowcount else "skipped"

    # ── Voting ────────────────────────────────────────────────────────────────

    async def vote_report(
        self,
        report_id: int,
        user_id: str,
        vote_type: str,
        session: AsyncSession | None = None,
    ) -> Dict:
        """
        Vote on a scam report with toggle/switch semantics.

        Args:
            report_id: Report ID.
            user_id: Voter user ID.
            vote_type: "approve" or "reject".

        Returns:
            {"success": True, "action": "voted"|"cancelled"|"switched"}
            or {"success": False, "error": "..."}
        """
        async with using_session(session) as s:
            # Check report exists and get reporter_user_id
            report_stmt = select(ScamReport.id, ScamReport.reporter_user_id).where(
                ScamReport.id == report_id
            )
            report_result = await s.execute(report_stmt)
            report_row = report_result.fetchone()

            if report_row is None:
                return {"success": False, "error": "report_not_found"}

            if report_row[1] == user_id:
                return {"success": False, "error": "cannot_vote_own_report"}

            # Anti-flood: check recent votes in last minute
            flood_stmt = (
                select(func.count())
                .select_from(ScamReportVote)
                .where(
                    ScamReportVote.user_id == user_id,
                    ScamReportVote.created_at
                    > datetime.now(UTC).replace(second=0, microsecond=0),
                )
            )
            flood_result = await s.execute(flood_stmt)
            # Note: The exact interval "1 minute" is hard to replicate perfectly
            # in ORM. Using a conservative approach.
            recent_count = flood_result.scalar_one() or 0
            if recent_count >= 5:
                return {"success": False, "error": "vote_too_fast"}

            # Check existing vote
            existing_stmt = select(ScamReportVote.vote_type).where(
                ScamReportVote.report_id == report_id,
                ScamReportVote.user_id == user_id,
            )
            existing_result = await s.execute(existing_stmt)
            existing_vote = existing_result.scalar_one_or_none()

            action: str

            if existing_vote is not None:
                old_vote = existing_vote

                if old_vote == vote_type:
                    # Toggle: remove vote
                    await s.execute(
                        delete(ScamReportVote).where(
                            ScamReportVote.report_id == report_id,
                            ScamReportVote.user_id == user_id,
                        )
                    )
                    if vote_type == "approve":
                        await s.execute(
                            update(ScamReport)
                            .where(ScamReport.id == report_id)
                            .values(
                                approve_count=func.greatest(
                                    0, ScamReport.approve_count - 1
                                ),
                                updated_at=datetime.now(UTC),
                            )
                        )
                    else:
                        await s.execute(
                            update(ScamReport)
                            .where(ScamReport.id == report_id)
                            .values(
                                reject_count=func.greatest(
                                    0, ScamReport.reject_count - 1
                                ),
                                updated_at=datetime.now(UTC),
                            )
                        )
                    action = "cancelled"
                else:
                    # Switch vote type
                    await s.execute(
                        update(ScamReportVote)
                        .where(
                            ScamReportVote.report_id == report_id,
                            ScamReportVote.user_id == user_id,
                        )
                        .values(vote_type=vote_type, created_at=datetime.now(UTC))
                    )
                    if old_vote == "approve":
                        await s.execute(
                            update(ScamReport)
                            .where(ScamReport.id == report_id)
                            .values(
                                approve_count=func.greatest(
                                    0, ScamReport.approve_count - 1
                                ),
                                reject_count=ScamReport.reject_count + 1,
                                updated_at=datetime.now(UTC),
                            )
                        )
                    else:
                        await s.execute(
                            update(ScamReport)
                            .where(ScamReport.id == report_id)
                            .values(
                                approve_count=ScamReport.approve_count + 1,
                                reject_count=func.greatest(
                                    0, ScamReport.reject_count - 1
                                ),
                                updated_at=datetime.now(UTC),
                            )
                        )
                    action = "switched"
            else:
                # New vote
                new_vote = ScamReportVote(
                    report_id=report_id,
                    user_id=user_id,
                    vote_type=vote_type,
                )
                s.add(new_vote)

                if vote_type == "approve":
                    await s.execute(
                        update(ScamReport)
                        .where(ScamReport.id == report_id)
                        .values(
                            approve_count=ScamReport.approve_count + 1,
                            updated_at=datetime.now(UTC),
                        )
                    )
                else:
                    await s.execute(
                        update(ScamReport)
                        .where(ScamReport.id == report_id)
                        .values(
                            reject_count=ScamReport.reject_count + 1,
                            updated_at=datetime.now(UTC),
                        )
                    )
                action = "voted"

            # Update verification status based on vote thresholds
            await self._update_verification_status(report_id, session=s)

            return {"success": True, "action": action}

    async def _update_verification_status(
        self,
        report_id: int,
        min_votes: int = 10,
        approve_rate_threshold: float = 0.7,
        session: AsyncSession | None = None,
    ) -> None:
        """
        Automatically update verification status based on vote counts.

        This is an internal helper — it expects the caller to pass the
        session if inside an existing transaction, or it creates its own.

        Args:
            report_id: Report ID.
            min_votes: Minimum total votes before status changes.
            approve_rate_threshold: Approval rate for "verified" status.
            session: Optional existing session.
        """
        async with using_session(session) as s:
            stmt = select(
                ScamReport.approve_count,
                ScamReport.reject_count,
            ).where(ScamReport.id == report_id)
            result = await s.execute(stmt)
            row = result.fetchone()
            if row is None:
                return

            approve, reject = row[0], row[1]
            total = approve + reject

            if total >= min_votes:
                approve_rate = approve / total if total > 0 else 0
                if approve_rate >= approve_rate_threshold:
                    new_status = "verified"
                elif approve_rate < 0.3:
                    new_status = "disputed"
                else:
                    new_status = "pending"
            else:
                new_status = "pending"

            await s.execute(
                update(ScamReport)
                .where(ScamReport.id == report_id)
                .values(
                    verification_status=new_status,
                    updated_at=datetime.now(UTC),
                )
            )

    # ── Comments ──────────────────────────────────────────────────────────────

    async def add_comment(
        self,
        report_id: int,
        user_id: str,
        content: str,
        transaction_hash: Optional[str] = None,
        session: AsyncSession | None = None,
    ) -> Dict:
        """
        Add a comment to a scam report.

        Note: Premium check and content validation are the caller's job —
        see ``add_comment_to_report`` in api/routers/scam_tracker/comments.py.

        Returns:
            {"success": True, "comment_id": int}
            or {"success": False, "error": "report_not_found"}
        """
        comment = ScamReportComment(
            report_id=report_id,
            user_id=user_id,
            content=content,
            transaction_hash=transaction_hash,
        )

        async with using_session(session) as s:
            # legacy 先查舉報存在；沒查的話 FK 違反在 flush 才炸，變成 500
            exists = await s.execute(
                select(ScamReport.id).where(ScamReport.id == report_id)
            )
            if exists.scalar_one_or_none() is None:
                return {"success": False, "error": "report_not_found"}

            s.add(comment)
            await s.flush()
            await s.refresh(comment)

            # Update comment count
            await s.execute(
                update(ScamReport)
                .where(ScamReport.id == report_id)
                .values(
                    comment_count=ScamReport.comment_count + 1,
                    updated_at=datetime.now(UTC),
                )
            )

            logger.info(
                "Comment %s added to report %s by %s",
                comment.id,
                report_id,
                user_id,
            )
            return {"success": True, "comment_id": comment.id}

    async def get_comments(
        self,
        report_id: int,
        limit: int = 50,
        offset: int = 0,
        session: AsyncSession | None = None,
    ) -> List[Dict]:
        """
        Get comments for a scam report.

        Args:
            report_id: Report ID.
            limit: Page size.
            offset: Pagination offset.

        Returns:
            List of comment dicts.
        """
        stmt = (
            select(ScamReportComment)
            .options(joinedload(ScamReportComment.user))
            .where(
                ScamReportComment.report_id == report_id,
                ScamReportComment.is_hidden == 0,
            )
            .order_by(ScamReportComment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        async with using_session(session) as s:
            result = await s.execute(stmt)
            rows = result.scalars().unique().all()
            return [_comment_row_to_dict(c) for c in rows]


scam_tracker_repo = ScamTrackerRepository()
