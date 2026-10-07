from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from api.middleware.rate_limit import limiter
from core.orm.action_guard_repo import action_guard_repo
from core.orm.models import GuardClient

router = APIRouter(prefix="/v1/guard", tags=["Guard API v1"])


class Notional(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=8)
    currency: str = Field(min_length=3, max_length=12, pattern=r"^[A-Za-z0-9_-]+$")


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")


class ActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_action_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    subject_ref: str = Field(min_length=1, max_length=256)
    action_type: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    asset_class: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    asset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")
    side: Literal["buy", "sell", "transfer", "other"]
    notional: Notional
    destination: Destination
    requested_at: datetime
    expires_at: datetime

    @field_validator("requested_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Guard timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_action_window(self):
        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")
        if self.expires_at - self.requested_at > timedelta(minutes=30):
            raise ValueError("Action validity window must not exceed 30 minutes")
        return self


class ApprovalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_at: datetime

    @field_validator("approved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at must include a timezone")
        return value


class OutcomeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["executed", "failed"]
    execution_ref_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReceiptVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


async def require_evaluate_key(
    x_guard_api_key: Annotated[str | None, Header()] = None,
) -> GuardClient:
    if not x_guard_api_key or len(x_guard_api_key) > 256:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    authenticated = await action_guard_repo.authenticate_key(
        x_guard_api_key, "guard:evaluate"
    )
    if authenticated is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return authenticated[0]


async def _require_scoped_key(raw_key: str | None, scope: str) -> GuardClient:
    if not raw_key or len(raw_key) > 256:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    authenticated = await action_guard_repo.authenticate_key(raw_key, scope)
    if authenticated is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return authenticated[0]


async def require_read_key(
    x_guard_api_key: Annotated[str | None, Header()] = None,
) -> GuardClient:
    return await _require_scoped_key(x_guard_api_key, "guard:decisions:read")


async def require_approval_key(
    x_guard_api_key: Annotated[str | None, Header()] = None,
) -> GuardClient:
    return await _require_scoped_key(x_guard_api_key, "guard:approvals:write")


async def require_outcome_key(
    x_guard_api_key: Annotated[str | None, Header()] = None,
) -> GuardClient:
    return await _require_scoped_key(x_guard_api_key, "guard:outcomes:write")


@router.post("/evaluate")
@limiter.limit("120/minute")
async def evaluate_external_action(
    request: Request,
    body: ActionDraft,
    client: GuardClient = Depends(require_evaluate_key),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise HTTPException(status_code=400, detail="Valid Idempotency-Key is required")
    draft = body.model_dump(mode="json")
    try:
        decision, receipt, replayed = await action_guard_repo.evaluate(
            client, draft, idempotency_key
        )
    except ValueError as exc:
        if str(exc) in {"IDEMPOTENCY_CONFLICT", "EXTERNAL_ACTION_CONFLICT"}:
            raise HTTPException(status_code=409, detail="Idempotency key payload conflict")
        if str(exc) == "NO_ACTIVE_POLICY":
            raise HTTPException(status_code=503, detail="No active Guard policy")
        raise
    return {
        "decision_id": decision.id,
        "decision": decision.decision,
        "reason_codes": decision.reason_codes,
        "state": decision.state,
        "policy_meaning": "Policy match only; not investment advice or execution authorization.",
        "replayed": replayed,
        "receipt": receipt,
    }


@router.get("/decisions/{decision_id}")
@limiter.limit("240/minute")
async def get_external_decision(
    request: Request,
    decision_id: str,
    client: GuardClient = Depends(require_read_key),
):
    decision = await action_guard_repo.get_decision(client.id, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Guard decision not found")
    return {
        "decision_id": decision.id,
        "decision": decision.decision,
        "reason_codes": decision.reason_codes,
        "state": decision.state,
        "action_summary": decision.action_summary,
        "outcome": decision.outcome,
    }


@router.post("/decisions/{decision_id}/approval")
@limiter.limit("60/minute")
async def record_external_approval(
    request: Request,
    decision_id: str,
    body: ApprovalEvidence,
    client: GuardClient = Depends(require_approval_key),
):
    try:
        result = await action_guard_repo.record_approval(
            client.id,
            decision_id,
            approved=body.approved,
            evidence_hash=body.evidence_hash,
            approved_at=body.approved_at,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="Decision is not pending approval")
    if result is None:
        raise HTTPException(status_code=404, detail="Guard decision not found")
    decision, receipt = result
    if decision.state == "expired":
        raise HTTPException(status_code=409, detail="Approval window expired")
    return {"decision_id": decision.id, "state": decision.state, "receipt": receipt}


@router.post("/decisions/{decision_id}/outcome")
@limiter.limit("120/minute")
async def record_external_outcome(
    request: Request,
    decision_id: str,
    body: OutcomeReport,
    client: GuardClient = Depends(require_outcome_key),
):
    try:
        result = await action_guard_repo.record_outcome(
            client.id,
            decision_id,
            outcome_status=body.status,
            execution_ref_hash=body.execution_ref_hash,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail="Decision is not executable")
    if result is None:
        raise HTTPException(status_code=404, detail="Guard decision not found")
    decision, receipt = result
    return {
        "decision_id": decision.id,
        "state": decision.state,
        "reported_by_client": True,
        "receipt": receipt,
    }


@router.post("/receipts/verify")
@limiter.limit("240/minute")
async def verify_external_receipt(
    request: Request,
    body: ReceiptVerificationRequest,
    client: GuardClient = Depends(require_read_key),
):
    verified = await action_guard_repo.verify_receipt(client.id, body.receipt_hash)
    if verified is None:
        raise HTTPException(status_code=404, detail="Guard receipt not found")
    return {"receipt_hash": body.receipt_hash, "valid": verified}
