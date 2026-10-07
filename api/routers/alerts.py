"""
Price Alerts API Router

Endpoints for managing user price alerts.
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from api.models import CreateAlertRequest
from api.utils import logger
from core.orm.alerts_repo import alerts_repo

router = APIRouter()


@router.post("/api/alerts")
@limiter.limit("10/minute")
async def create_alert_endpoint(
    request: Request,
    req: CreateAlertRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a new price alert."""
    user_id = current_user["user_id"]

    try:
        alert = await alerts_repo.create_alert(
            user_id=user_id,
            symbol=req.symbol,
            market=req.market,
            condition=req.condition,
            target=req.target,
            repeat=req.repeat,
        )
        return {"success": True, "alert": alert}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"建立警報失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to create alert")


@router.get("/api/alerts")
async def get_alerts_endpoint(current_user: dict = Depends(get_current_user)):
    """Get all alerts for the current user."""
    user_id = current_user["user_id"]
    try:
        alerts = await alerts_repo.get_user_alerts(user_id)
        return {"success": True, "alerts": alerts}
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"獲取警報失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch alerts")


@router.delete("/api/alerts/{alert_id}")
@limiter.limit("30/minute")
async def delete_alert_endpoint(
    request: Request,
    alert_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a specific alert (ownership enforced)."""
    user_id = current_user["user_id"]
    try:
        deleted = await alerts_repo.delete_alert(alert_id, user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Alert not found or you do not have permission to delete it")
        return {"success": True, "message": "Alert deleted"}
    except HTTPException:
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        logger.error(f"刪除警報失敗: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete alert")
