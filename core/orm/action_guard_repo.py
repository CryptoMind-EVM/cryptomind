from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Numeric, and_, cast, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.action_guard.policy import evaluate_action
from core.action_guard.receipts import create_signed_receipt

from .models import (
    ActionReceipt,
    GuardApiKey,
    GuardClient,
    GuardDecisionRecord,
    GuardPolicy,
)
from .session import using_session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _receipt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY", "")
    if len(secret) < 16:
        raise RuntimeError("JWT_SECRET_KEY is required for Guard receipts")
    return secret


class ActionGuardRepository:
    async def _append_receipt(
        self,
        session: AsyncSession,
        *,
        client_id: str | None = None,
        user_id: str | None = None,
        decision_id: str | None = None,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not client_id and not user_id:
            raise ValueError("A receipt requires a client_id or user_id")
        chain_scope = (
            ActionReceipt.client_id == client_id
            if client_id
            else and_(
                ActionReceipt.user_id == user_id,
                ActionReceipt.client_id.is_(None),
            )
        )
        # Serialize appends per chain so concurrent requests cannot sign two
        # receipts with the same previous hash. The lock is transaction-scoped.
        chain_key = f"client:{client_id}" if client_id else f"user:{user_id}"
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:scope))"),
            {"scope": chain_key},
        )
        previous_hash = await session.scalar(
            select(ActionReceipt.receipt_hash)
            .where(chain_scope)
            .order_by(ActionReceipt.created_at.desc(), ActionReceipt.id.desc())
            .limit(1)
        )
        signed = create_signed_receipt(
            {**payload, "issued_at": _utcnow().isoformat()},
            secret=_receipt_secret(),
            previous_hash=previous_hash or "",
        )
        receipt = ActionReceipt(
            id=f"grcp_{uuid.uuid4().hex}",
            client_id=client_id,
            user_id=user_id,
            decision_id=decision_id,
            event_type=event_type,
            payload=signed["payload"],
            previous_hash=signed["previous_hash"],
            receipt_hash=signed["receipt_hash"],
            signature=signed["signature"],
        )
        session.add(receipt)
        await session.flush()
        return self._receipt_dict(receipt)

    async def append_internal_receipt(
        self,
        *,
        user_id: str,
        event_type: str,
        payload: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        """Append an audit receipt for CryptoMind's own consent/control events."""
        if not user_id:
            raise ValueError("user_id is required")
        async with using_session(session) as s:
            return await self._append_receipt(
                s,
                user_id=user_id,
                event_type=event_type,
                payload=payload,
            )

    async def create_client(
        self,
        owner_user_id: str,
        name: str,
        environment: str,
        session: AsyncSession | None = None,
    ) -> GuardClient:
        client = GuardClient(
            id=f"gcl_{uuid.uuid4().hex}",
            owner_user_id=owner_user_id,
            name=name,
            environment=environment,
            status="active",
        )
        async with using_session(session) as s:
            s.add(client)
            await s.flush()
        return client

    async def list_clients(
        self, owner_user_id: str, session: AsyncSession | None = None
    ) -> list[GuardClient]:
        stmt = (
            select(GuardClient)
            .where(GuardClient.owner_user_id == owner_user_id)
            .order_by(GuardClient.created_at.desc())
        )
        async with using_session(session) as s:
            return list((await s.scalars(stmt)).all())

    async def get_client_dashboard(
        self,
        owner_user_id: str,
        client_id: str,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        async with using_session(session) as s:
            client = await self._owned_client(client_id, owner_user_id, s)
            if client is None:
                return None
            keys = list(
                (
                    await s.scalars(
                        select(GuardApiKey)
                        .where(GuardApiKey.client_id == client_id)
                        .order_by(GuardApiKey.created_at.desc())
                    )
                ).all()
            )
            policies = list(
                (
                    await s.scalars(
                        select(GuardPolicy)
                        .where(GuardPolicy.client_id == client_id)
                        .order_by(GuardPolicy.version.desc())
                    )
                ).all()
            )
            count_rows = (
                await s.execute(
                    select(
                        GuardDecisionRecord.state,
                        func.count(GuardDecisionRecord.id),
                    )
                    .where(GuardDecisionRecord.client_id == client_id)
                    .group_by(GuardDecisionRecord.state)
                )
            ).all()
            return {
                "client": client,
                "keys": keys,
                "policies": policies,
                "decision_counts": {state: int(count) for state, count in count_rows},
            }

    async def list_owner_decisions(
        self,
        owner_user_id: str,
        client_id: str,
        *,
        state: str | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> list[GuardDecisionRecord] | None:
        async with using_session(session) as s:
            if await self._owned_client(client_id, owner_user_id, s) is None:
                return None
            stmt = select(GuardDecisionRecord).where(
                GuardDecisionRecord.client_id == client_id
            )
            if state:
                stmt = stmt.where(GuardDecisionRecord.state == state)
            stmt = stmt.order_by(GuardDecisionRecord.created_at.desc()).limit(limit)
            return list((await s.scalars(stmt)).all())

    async def list_owner_receipts(
        self,
        owner_user_id: str,
        *,
        client_id: str | None = None,
        limit: int = 50,
        session: AsyncSession | None = None,
    ) -> list[ActionReceipt] | None:
        async with using_session(session) as s:
            if client_id:
                if await self._owned_client(client_id, owner_user_id, s) is None:
                    return None
                scope = ActionReceipt.client_id == client_id
            else:
                owned_clients = select(GuardClient.id).where(
                    GuardClient.owner_user_id == owner_user_id
                )
                scope = or_(
                    and_(
                        ActionReceipt.user_id == owner_user_id,
                        ActionReceipt.client_id.is_(None),
                    ),
                    ActionReceipt.client_id.in_(owned_clients),
                )
            stmt = (
                select(ActionReceipt)
                .where(scope)
                .order_by(ActionReceipt.created_at.desc())
                .limit(limit)
            )
            return list((await s.scalars(stmt)).all())

    async def _owned_client(
        self, client_id: str, owner_user_id: str, session: AsyncSession
    ) -> GuardClient | None:
        return await session.scalar(
            select(GuardClient).where(
                GuardClient.id == client_id,
                GuardClient.owner_user_id == owner_user_id,
            )
        )

    async def create_api_key(
        self,
        client_id: str,
        owner_user_id: str,
        scopes: list[str],
        session: AsyncSession | None = None,
    ) -> tuple[GuardApiKey, str] | None:
        async with using_session(session) as s:
            client = await self._owned_client(client_id, owner_user_id, s)
            if client is None:
                return None
            raw_key = f"cmg_{client.environment}_{secrets.token_urlsafe(32)}"
            record = GuardApiKey(
                id=f"gkey_{uuid.uuid4().hex}",
                client_id=client_id,
                key_prefix=raw_key[:16],
                key_hash=_key_hash(raw_key),
                scopes=scopes,
            )
            s.add(record)
            await s.flush()
        return record, raw_key

    async def revoke_api_key(
        self,
        client_id: str,
        key_id: str,
        owner_user_id: str,
        session: AsyncSession | None = None,
    ) -> bool:
        async with using_session(session) as s:
            if await self._owned_client(client_id, owner_user_id, s) is None:
                return False
            result = await s.execute(
                update(GuardApiKey)
                .where(
                    GuardApiKey.id == key_id,
                    GuardApiKey.client_id == client_id,
                    GuardApiKey.revoked_at.is_(None),
                )
                .values(revoked_at=_utcnow())
            )
            return bool(result.rowcount)

    async def create_policy(
        self,
        client_id: str,
        owner_user_id: str,
        document: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> GuardPolicy | None:
        async with using_session(session) as s:
            if await self._owned_client(client_id, owner_user_id, s) is None:
                return None
            latest = await s.scalar(
                select(func.max(GuardPolicy.version)).where(
                    GuardPolicy.client_id == client_id
                )
            )
            await s.execute(
                update(GuardPolicy)
                .where(
                    GuardPolicy.client_id == client_id,
                    GuardPolicy.is_active.is_(True),
                )
                .values(is_active=False)
            )
            policy = GuardPolicy(
                id=f"gpol_{uuid.uuid4().hex}",
                client_id=client_id,
                version=int(latest or 0) + 1,
                document=document,
                is_active=True,
                created_by=owner_user_id,
            )
            s.add(policy)
            await s.flush()
            return policy

    async def authenticate_key(
        self,
        raw_key: str,
        required_scope: str,
        session: AsyncSession | None = None,
    ) -> tuple[GuardClient, GuardApiKey] | None:
        now = _utcnow()
        stmt = (
            select(GuardClient, GuardApiKey)
            .join(GuardApiKey, GuardApiKey.client_id == GuardClient.id)
            .where(
                GuardApiKey.key_hash == _key_hash(raw_key),
                GuardApiKey.revoked_at.is_(None),
                GuardClient.status == "active",
            )
        )
        async with using_session(session) as s:
            row = (await s.execute(stmt)).one_or_none()
            if row is None:
                return None
            client, key = row
            if key.expires_at is not None and key.expires_at <= now:
                return None
            if required_scope not in (key.scopes or []):
                return None
            key.last_used_at = now
            return client, key

    async def evaluate(
        self,
        client: GuardClient,
        draft: dict[str, Any],
        idempotency_key: str,
        session: AsyncSession | None = None,
    ) -> tuple[GuardDecisionRecord, dict[str, Any], bool]:
        fingerprint = hashlib.sha256(
            __import__("json").dumps(
                draft, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        async with using_session(session) as s:
            existing = await s.scalar(
                select(GuardDecisionRecord).where(
                    GuardDecisionRecord.client_id == client.id,
                    GuardDecisionRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.request_fingerprint != fingerprint:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                receipt = await s.scalar(
                    select(ActionReceipt)
                    .where(ActionReceipt.decision_id == existing.id)
                    .order_by(ActionReceipt.created_at.desc())
                )
                return existing, self._receipt_dict(receipt), True

            existing_action = await s.scalar(
                select(GuardDecisionRecord).where(
                    GuardDecisionRecord.client_id == client.id,
                    GuardDecisionRecord.external_action_id
                    == draft["external_action_id"],
                )
            )
            if existing_action is not None:
                raise ValueError("EXTERNAL_ACTION_CONFLICT")

            policy = await s.scalar(
                select(GuardPolicy).where(
                    GuardPolicy.client_id == client.id,
                    GuardPolicy.is_active.is_(True),
                )
            )
            if policy is None:
                raise ValueError("NO_ACTIVE_POLICY")

            currency = draft["notional"]["currency"]
            amount_expr = cast(
                GuardDecisionRecord.action_summary["amount"].astext, Numeric
            )
            used_today = await s.scalar(
                select(func.coalesce(func.sum(amount_expr), 0)).where(
                    GuardDecisionRecord.client_id == client.id,
                    GuardDecisionRecord.state == "executed",
                    GuardDecisionRecord.created_at >= func.date_trunc("day", func.now()),
                    GuardDecisionRecord.action_summary["currency"].astext == currency,
                )
            )
            result = evaluate_action(
                draft, policy.document, executed_notional_today=used_today
            )
            decision_id = f"gdec_{uuid.uuid4().hex}"
            state_map = {
                "ALLOW": "allowed",
                "DENY": "denied",
                "REQUIRE_APPROVAL": "pending_approval",
            }
            summary = {
                "action_type": draft["action_type"],
                "asset_class": draft["asset_class"],
                "asset_id": draft["asset_id"],
                "side": draft.get("side"),
                "amount": str(draft["notional"]["amount"]),
                "currency": currency,
                "venue_id": draft["destination"]["venue_id"],
            }
            record = GuardDecisionRecord(
                id=decision_id,
                client_id=client.id,
                external_action_id=draft["external_action_id"],
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                subject_ref_hash=hashlib.sha256(
                    draft["subject_ref"].encode("utf-8")
                ).hexdigest(),
                action_summary=summary,
                policy_id=policy.id,
                decision=result.decision.value,
                reason_codes=list(result.reason_codes),
                state=state_map[result.decision.value],
                approval_expires_at=(
                    datetime.fromisoformat(draft["expires_at"].replace("Z", "+00:00"))
                    if result.decision.value == "REQUIRE_APPROVAL"
                    else None
                ),
            )
            s.add(record)
            await s.flush()

            receipt_dict = await self._append_receipt(
                s,
                client_id=client.id,
                decision_id=decision_id,
                event_type="decision.created",
                payload={
                    "decision_id": decision_id,
                    "client_id": client.id,
                    "external_action_id": draft["external_action_id"],
                    "subject_ref": draft["subject_ref"],
                    "decision": result.decision.value,
                    "reason_codes": list(result.reason_codes),
                    "policy_version": policy.version,
                    "action_summary": summary,
                },
            )
            return record, receipt_dict, False

    async def get_decision(
        self,
        client_id: str,
        decision_id: str,
        session: AsyncSession | None = None,
    ) -> GuardDecisionRecord | None:
        async with using_session(session) as s:
            return await s.scalar(
                select(GuardDecisionRecord).where(
                    GuardDecisionRecord.id == decision_id,
                    GuardDecisionRecord.client_id == client_id,
                )
            )

    async def record_approval(
        self,
        client_id: str,
        decision_id: str,
        *,
        approved: bool,
        evidence_hash: str,
        approved_at: datetime,
        session: AsyncSession | None = None,
    ) -> tuple[GuardDecisionRecord, dict[str, Any]] | None:
        async with using_session(session) as s:
            record = await s.scalar(
                select(GuardDecisionRecord)
                .where(
                    GuardDecisionRecord.id == decision_id,
                    GuardDecisionRecord.client_id == client_id,
                )
                .with_for_update()
            )
            if record is None:
                return None
            if record.state != "pending_approval":
                raise ValueError("INVALID_DECISION_STATE")
            now = _utcnow()
            if record.approval_expires_at is None or record.approval_expires_at <= now:
                record.state = "expired"
                receipt = await self._append_receipt(
                    s,
                    client_id=client_id,
                    decision_id=decision_id,
                    event_type="decision.expired",
                    payload={
                        "decision_id": decision_id,
                        "client_id": client_id,
                        "reason_codes": ["APPROVAL_EXPIRED"],
                    },
                )
                return record, receipt
            if approved_at > now or approved_at < record.created_at:
                raise ValueError("INVALID_APPROVAL_TIME")
            record.state = "approved" if approved else "denied"
            receipt = await self._append_receipt(
                s,
                client_id=client_id,
                decision_id=decision_id,
                event_type="decision.approval",
                payload={
                    "decision_id": decision_id,
                    "client_id": client_id,
                    "approved": approved,
                    "evidence_hash": evidence_hash,
                    "approved_at": approved_at.isoformat(),
                },
            )
            return record, receipt

    async def record_outcome(
        self,
        client_id: str,
        decision_id: str,
        *,
        outcome_status: str,
        execution_ref_hash: str,
        session: AsyncSession | None = None,
    ) -> tuple[GuardDecisionRecord, dict[str, Any]] | None:
        async with using_session(session) as s:
            record = await s.scalar(
                select(GuardDecisionRecord)
                .where(
                    GuardDecisionRecord.id == decision_id,
                    GuardDecisionRecord.client_id == client_id,
                )
                .with_for_update()
            )
            if record is None:
                return None
            if record.state not in {"allowed", "approved"}:
                raise ValueError("INVALID_DECISION_STATE")
            record.state = outcome_status
            record.outcome = {
                "status": outcome_status,
                "execution_ref_hash": execution_ref_hash,
                "reported_by_client": True,
                "reported_at": _utcnow().isoformat(),
            }
            receipt = await self._append_receipt(
                s,
                client_id=client_id,
                decision_id=decision_id,
                event_type="decision.outcome_reported",
                payload={
                    "decision_id": decision_id,
                    "client_id": client_id,
                    **record.outcome,
                },
            )
            return record, receipt

    async def verify_receipt(
        self,
        client_id: str,
        receipt_hash: str,
        session: AsyncSession | None = None,
    ) -> bool | None:
        from core.action_guard.receipts import verify_signed_receipt

        async with using_session(session) as s:
            receipt = await s.scalar(
                select(ActionReceipt).where(
                    ActionReceipt.client_id == client_id,
                    ActionReceipt.receipt_hash == receipt_hash,
                )
            )
            if receipt is None:
                return None
            return verify_signed_receipt(
                {
                    "version": "cm-guard-receipt-v1",
                    "previous_hash": receipt.previous_hash,
                    "payload": receipt.payload,
                    "receipt_hash": receipt.receipt_hash,
                    "signature": receipt.signature,
                },
                secret=_receipt_secret(),
            )

    @staticmethod
    def _receipt_dict(receipt: ActionReceipt | None) -> dict[str, Any]:
        if receipt is None:
            return {}
        return {
            "version": "cm-guard-receipt-v1",
            "previous_hash": receipt.previous_hash,
            "payload": receipt.payload,
            "receipt_hash": receipt.receipt_hash,
            "signature": receipt.signature,
        }


action_guard_repo = ActionGuardRepository()
