"""檢舉附風險分數（c065）：被檢舉的內容先打分數，後台照危險程度排，管理員先看最危險的。

在背景跑（送出檢舉不用等模型）；檢查服務不在就不寫，後台那筆排在有分數的後面。
論壇檢舉只給後台用，社群投票佇列不顯示分數；私訊只在被檢舉時才打分數，不主動掃。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional, Set

from sqlalchemy import select, update

from core.moderation.service import risk_of

logger = logging.getLogger(__name__)

# 私訊：被檢舉的那個人在快照裡最近幾則（詐騙常拆成好幾則傳）
DM_RECENT_MESSAGES = 5

_tasks: Set[asyncio.Task] = set()


def spawn(coro) -> None:
    """背景跑、不拖慢回應；持有參照到跑完（不然可能被 GC 中途丟掉）。"""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _content_text(session, content_type: str, content_id: int) -> Optional[str]:
    from core.orm.models import ForumComment, Post

    if content_type == "post":
        row = (
            await session.execute(
                select(Post.title, Post.content).where(Post.id == content_id)
            )
        ).first()
        return f"{row[0] or ''}\n{row[1] or ''}" if row else None
    if content_type == "comment":
        row = (
            await session.execute(
                select(ForumComment.content).where(ForumComment.id == content_id)
            )
        ).first()
        return row[0] if row else None
    return None


async def score_content_report(
    report_id: int, content_type: str, content_id: int, session=None
) -> Optional[dict]:
    from core.orm.models import ContentReport
    from core.orm.session import using_session

    try:
        async with using_session(session) as s:
            text = await _content_text(s, content_type, content_id)
        risk = await risk_of(text or "")
        if risk is None:
            return None
        async with using_session(session) as s:
            await s.execute(
                update(ContentReport)
                .where(ContentReport.id == report_id)
                .values(risk_score=risk["score"], risk_category=risk["category"])
            )
        return risk
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001 — 排序用的附加資訊，失敗不影響檢舉本身
        logger.warning(
            "[moderation] score content report %s failed: %s", report_id, exc
        )
        return None


def dm_report_text(snapshot: Iterable[dict], reported_user_id: str) -> str:
    """被檢舉的人在快照裡最近幾則（只看他傳的，檢舉人的話不算進他的風險）。"""
    mine = [
        m.get("content") or ""
        for m in snapshot
        if m.get("from_user_id") == reported_user_id
    ]
    return "\n".join(mine[-DM_RECENT_MESSAGES:])


async def _score_snapshot_report(
    model, label: str, report_id: int, text: str, session=None
) -> Optional[dict]:
    """私訊／群組檢舉共用：替快照打風險分數寫回那筆檢舉（後台照危險程度排）"""
    from core.orm.session import using_session

    try:
        risk = await risk_of(text)
        if risk is None:
            return None
        async with using_session(session) as s:
            await s.execute(
                update(model)
                .where(model.id == report_id)
                .values(risk_score=risk["score"], risk_category=risk["category"])
            )
        return risk
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[moderation] score %s report %s failed: %s", label, report_id, exc
        )
        return None


async def score_dm_report(report_id: int, text: str, session=None) -> Optional[dict]:
    from core.orm.models import DmReport

    return await _score_snapshot_report(DmReport, "dm", report_id, text, session)


async def score_group_report(report_id: int, text: str, session=None) -> Optional[dict]:
    from core.orm.models import GroupReport

    return await _score_snapshot_report(GroupReport, "group", report_id, text, session)
