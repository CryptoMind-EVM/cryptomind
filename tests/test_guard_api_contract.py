from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from api.routers.guard_management import (
    CreateClientRequest,
    CreateKeyRequest,
    PolicyDocument,
)
from api.routers.guard_v1 import ActionDraft


def _action_payload():
    now = datetime.now(timezone.utc)
    return {
        "external_action_id": "action-1",
        "subject_ref": "subject-1",
        "action_type": "trade",
        "asset_class": "crypto",
        "asset_id": "TON",
        "side": "buy",
        "notional": {"amount": "25", "currency": "USD"},
        "destination": {"venue_id": "venue-a"},
        "requested_at": now,
        "expires_at": now + timedelta(minutes=5),
    }


def test_action_draft_accepts_policy_inputs_only():
    draft = ActionDraft.model_validate(_action_payload())

    assert draft.external_action_id == "action-1"
    assert str(draft.notional.amount) == "25"


@pytest.mark.parametrize("field", ["private_key", "calldata", "serialized_transaction"])
def test_action_draft_rejects_transaction_material(field):
    payload = _action_payload()
    payload[field] = "forbidden"

    with pytest.raises(ValidationError):
        ActionDraft.model_validate(payload)


def test_external_api_key_cannot_modify_guard_policy():
    with pytest.raises(ValidationError):
        CreateKeyRequest(scopes=["guard:policies:write"])


def test_policy_limit_order_is_validated():
    with pytest.raises(ValidationError):
        PolicyDocument(
            allowed_action_types=["trade"],
            allowed_asset_classes=["crypto"],
            allowed_venues=["venue-a"],
            allowed_currencies=["USD"],
            max_notional_per_action="100",
            max_notional_per_day="90",
            require_approval_at="50",
        )


def test_guard_client_name_is_trimmed_and_non_blank():
    with pytest.raises(ValidationError):
        CreateClientRequest(name="   ")
