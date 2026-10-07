from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.deps import get_current_user
from api.middleware.rate_limit import limiter
from core.orm.action_guard_repo import action_guard_repo

router = APIRouter(prefix="/api/guard", tags=["Guard Management"])

_SCOPES = {
    "guard:evaluate",
    "guard:decisions:read",
    "guard:approvals:write",
    "guard:outcomes:write",
}


class CreateClientRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=80)
    environment: Literal["test", "live"] = "live"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Guard client name must contain at least 2 characters")
        return value


class CreateKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes: list[str] = Field(
        default_factory=lambda: ["guard:evaluate", "guard:decisions:read"],
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_scopes(self):
        scopes = set(self.scopes)
        if len(scopes) != len(self.scopes) or not scopes.issubset(_SCOPES):
            raise ValueError("Invalid or duplicate Guard API scope")
        return self


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_action_types: list[str] = Field(min_length=1, max_length=20)
    allowed_asset_classes: list[str] = Field(min_length=1, max_length=20)
    allowed_venues: list[str] = Field(min_length=1, max_length=100)
    allowed_currencies: list[str] = Field(min_length=1, max_length=20)
    max_notional_per_action: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    max_notional_per_day: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    require_approval_at: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    denied_assets: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_limits(self):
        if self.require_approval_at > self.max_notional_per_action:
            raise ValueError("Approval threshold must not exceed per-action limit")
        if self.max_notional_per_action > self.max_notional_per_day:
            raise ValueError("Per-action limit must not exceed daily limit")
        return self


@router.get("/clients")
async def list_guard_clients(current_user: dict = Depends(get_current_user)):
    clients = await action_guard_repo.list_clients(current_user["user_id"])
    return {
        "clients": [
            {
                "id": item.id,
                "name": item.name,
                "status": item.status,
                "environment": item.environment,
                "created_at": item.created_at,
            }
            for item in clients
        ]
    }


def _serialize_receipt(item) -> dict:
    return {
        "id": item.id,
        "client_id": item.client_id,
        "decision_id": item.decision_id,
        "event_type": item.event_type,
        "payload": item.payload,
        "previous_hash": item.previous_hash,
        "receipt_hash": item.receipt_hash,
        "signature": item.signature,
        "created_at": item.created_at,
    }


@router.get("/clients/{client_id}")
@limiter.limit("60/minute")
async def get_guard_client_dashboard(
    request: Request,
    client_id: str,
    current_user: dict = Depends(get_current_user),
):
    dashboard = await action_guard_repo.get_client_dashboard(
        current_user["user_id"], client_id
    )
    if dashboard is None:
        raise HTTPException(status_code=404, detail="Guard client not found")
    client = dashboard["client"]
    return {
        "client": {
            "id": client.id,
            "name": client.name,
            "status": client.status,
            "environment": client.environment,
            "created_at": client.created_at,
        },
        "keys": [
            {
                "id": item.id,
                "prefix": item.key_prefix,
                "scopes": item.scopes,
                "expires_at": item.expires_at,
                "revoked_at": item.revoked_at,
                "last_used_at": item.last_used_at,
                "created_at": item.created_at,
            }
            for item in dashboard["keys"]
        ],
        "policies": [
            {
                "id": item.id,
                "version": item.version,
                "document": item.document,
                "is_active": item.is_active,
                "created_at": item.created_at,
            }
            for item in dashboard["policies"]
        ],
        "decision_counts": dashboard["decision_counts"],
    }


@router.get("/clients/{client_id}/decisions")
@limiter.limit("60/minute")
async def list_guard_client_decisions(
    request: Request,
    client_id: str,
    state: str | None = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    allowed_states = {
        "allowed", "denied", "pending_approval", "approved",
        "executed", "failed", "expired",
    }
    if state and state not in allowed_states:
        raise HTTPException(status_code=400, detail="Invalid decision state")
    records = await action_guard_repo.list_owner_decisions(
        current_user["user_id"], client_id, state=state, limit=limit
    )
    if records is None:
        raise HTTPException(status_code=404, detail="Guard client not found")
    return {
        "decisions": [
            {
                "id": item.id,
                "external_action_id": item.external_action_id,
                "decision": item.decision,
                "state": item.state,
                "reason_codes": item.reason_codes,
                "action_summary": item.action_summary,
                "outcome": item.outcome,
                "created_at": item.created_at,
            }
            for item in records
        ]
    }


@router.get("/clients/{client_id}/receipts")
@limiter.limit("60/minute")
async def list_guard_client_receipts(
    request: Request,
    client_id: str,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    records = await action_guard_repo.list_owner_receipts(
        current_user["user_id"], client_id=client_id, limit=limit
    )
    if records is None:
        raise HTTPException(status_code=404, detail="Guard client not found")
    return {"receipts": [_serialize_receipt(item) for item in records]}


@router.get("/activity/receipts")
@limiter.limit("60/minute")
async def list_my_guard_receipts(
    request: Request,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
    records = await action_guard_repo.list_owner_receipts(
        current_user["user_id"], limit=limit
    )
    return {"receipts": [_serialize_receipt(item) for item in (records or [])]}


@router.post("/clients", status_code=201)
@limiter.limit("10/hour")
async def create_guard_client(
    request: Request,
    body: CreateClientRequest,
    current_user: dict = Depends(get_current_user),
):
    client = await action_guard_repo.create_client(
        current_user["user_id"], body.name.strip(), body.environment
    )
    return {
        "id": client.id,
        "name": client.name,
        "status": client.status,
        "environment": client.environment,
    }


@router.post("/clients/{client_id}/keys", status_code=201)
@limiter.limit("10/hour")
async def create_guard_api_key(
    request: Request,
    client_id: str,
    body: CreateKeyRequest,
    current_user: dict = Depends(get_current_user),
):
    created = await action_guard_repo.create_api_key(
        client_id, current_user["user_id"], body.scopes
    )
    if created is None:
        raise HTTPException(status_code=404, detail="Guard client not found")
    record, raw_key = created
    return {
        "id": record.id,
        "prefix": record.key_prefix,
        "api_key": raw_key,
        "scopes": record.scopes,
        "warning": "Copy this key now. It is stored only as a hash and is not shown again.",
    }


@router.delete("/clients/{client_id}/keys/{key_id}", status_code=204)
@limiter.limit("20/hour")
async def revoke_guard_api_key(
    request: Request,
    client_id: str,
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    revoked = await action_guard_repo.revoke_api_key(
        client_id, key_id, current_user["user_id"]
    )
    if not revoked:
        raise HTTPException(status_code=404, detail="Guard API key not found")


@router.post("/clients/{client_id}/policies", status_code=201)
@limiter.limit("20/hour")
async def create_guard_policy(
    request: Request,
    client_id: str,
    body: PolicyDocument,
    current_user: dict = Depends(get_current_user),
):
    policy = await action_guard_repo.create_policy(
        client_id,
        current_user["user_id"],
        body.model_dump(mode="json"),
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="Guard client not found")
    return {
        "id": policy.id,
        "client_id": policy.client_id,
        "version": policy.version,
        "is_active": policy.is_active,
        "document": policy.document,
    }
