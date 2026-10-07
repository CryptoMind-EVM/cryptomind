from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.routers import guard_management
from core.orm.action_guard_repo import ActionGuardRepository

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/guard/clients/gcl_1",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
async def test_guard_client_dashboard_never_returns_key_hash(monkeypatch):
    repo = SimpleNamespace(
        get_client_dashboard=AsyncMock(
            return_value={
                "client": SimpleNamespace(
                    id="gcl_1",
                    name="Executor",
                    status="active",
                    environment="test",
                    created_at=NOW,
                ),
                "keys": [
                    SimpleNamespace(
                        id="gkey_1",
                        key_prefix="cmg_test_demo",
                        key_hash="server-only-hash",
                        scopes=["guard:evaluate"],
                        expires_at=None,
                        revoked_at=None,
                        last_used_at=None,
                        created_at=NOW,
                    )
                ],
                "policies": [
                    SimpleNamespace(
                        id="gpol_1",
                        version=2,
                        document={"allowed_action_types": ["trade"]},
                        is_active=True,
                        created_at=NOW,
                    )
                ],
                "decision_counts": {"allowed": 3, "denied": 1},
            }
        )
    )
    monkeypatch.setattr(guard_management, "action_guard_repo", repo)

    result = await guard_management.get_guard_client_dashboard(
        _request(), "gcl_1", current_user={"user_id": "user-1"}
    )

    assert result["client"]["id"] == "gcl_1"
    assert result["keys"][0]["prefix"] == "cmg_test_demo"
    assert "key_hash" not in result["keys"][0]
    assert result["policies"][0]["version"] == 2
    assert result["decision_counts"] == {"allowed": 3, "denied": 1}


@pytest.mark.asyncio
async def test_guard_client_dashboard_is_owner_scoped(monkeypatch):
    repo = SimpleNamespace(get_client_dashboard=AsyncMock(return_value=None))
    monkeypatch.setattr(guard_management, "action_guard_repo", repo)

    with pytest.raises(HTTPException) as exc:
        await guard_management.get_guard_client_dashboard(
            _request(), "gcl_other", current_user={"user_id": "user-1"}
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_internal_receipt_is_user_scoped_and_has_no_decision_fk():
    session = MagicMock()
    session.scalar = AsyncMock(return_value="a" * 64)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    repo = ActionGuardRepository()

    result = await repo.append_internal_receipt(
        user_id="user-1",
        event_type="agent.consent",
        payload={"approved": True, "tools": ["wallet_lookup"]},
        session=session,
    )

    stored = session.add.call_args.args[0]
    assert stored.user_id == "user-1"
    assert stored.client_id is None
    assert stored.decision_id is None
    assert result["payload"]["approved"] is True
    assert len(result["receipt_hash"]) == 64
    lock_stmt, lock_params = session.execute.await_args.args
    assert "pg_advisory_xact_lock" in str(lock_stmt)
    assert lock_params == {"scope": "user:user-1"}
