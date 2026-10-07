"""
看板相關 API
"""

import asyncio

from fastapi import APIRouter, HTTPException

from core.orm.forum_repo import forum_repo

router = APIRouter(prefix="/api/forum/boards", tags=["Forum - Boards"])


@router.get("")
async def list_boards():
    """
    獲取所有啟用的看板列表

    Returns:
        - boards: 看板列表
    """
    try:
        boards = await forum_repo.get_boards(active_only=True)
        return {"success": True, "boards": boards}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch board list, please try again later")


@router.get("/{slug}")
async def get_board(slug: str):
    """
    獲取指定看板詳情

    Args:
        slug: 看板 slug (如 "crypto")

    Returns:
        - board: 看板詳情
    """
    try:
        board = await forum_repo.get_board_by_slug(slug)
        if not board:
            raise HTTPException(status_code=404, detail="Board not found")
        return {"success": True, "board": board}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to fetch board details, please try again later")
