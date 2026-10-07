"""
Admin Panel Router Module
Combines notifications, users, forum, config, and stats endpoints
"""

from fastapi import APIRouter

from .config import router as config_router
from .dm_reports import router as dm_reports_router
from .forum import router as forum_router
from .group_reports import router as group_reports_router
from .notifications import router as notifications_router
from .schemas import (
    BroadcastRequest,
    PostPinRequest,
    PostVisibilityRequest,
    ResolveReportRequest,
    SetMembershipRequest,
    SetRoleRequest,
    SetStatusRequest,
    UpdateConfigRequest,
)
from .settings_center import router as settings_center_router
from .stats import router as stats_router
from .users import router as users_router

router = APIRouter(prefix="/api/admin", tags=["Admin Panel"])
router.include_router(notifications_router)
router.include_router(users_router)
router.include_router(forum_router)
router.include_router(dm_reports_router)
router.include_router(group_reports_router)
router.include_router(config_router)
router.include_router(stats_router)
router.include_router(settings_center_router)


# Re-export all for backward compatibility
__all__ = [
    "router",
    "notifications_router",
    "users_router",
    "forum_router",
    "config_router",
    "stats_router",
    "BroadcastRequest",
    "SetRoleRequest",
    "SetMembershipRequest",
    "SetStatusRequest",
    "PostVisibilityRequest",
    "PostPinRequest",
    "ResolveReportRequest",
    "UpdateConfigRequest",
]
