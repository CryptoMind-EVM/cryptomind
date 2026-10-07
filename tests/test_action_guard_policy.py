from datetime import datetime, timedelta, timezone

import pytest

from core.action_guard.policy import evaluate_action
from core.action_guard.types import GuardDecision

NOW = datetime(2026, 8, 13, 8, 0, tzinfo=timezone.utc)


def _draft(**overrides):
    draft = {
        "external_action_id": "act_123",
        "subject_ref": "customer-42",
        "action_type": "trade",
        "asset_class": "crypto",
        "asset_id": "TON",
        "side": "buy",
        "notional": {"amount": "25.00", "currency": "USD"},
        "destination": {"venue_id": "venue-a"},
        "requested_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
    }
    draft.update(overrides)
    return draft


def _policy(**overrides):
    policy = {
        "allowed_action_types": ["trade"],
        "allowed_asset_classes": ["crypto", "equity"],
        "allowed_venues": ["venue-a"],
        "allowed_currencies": ["USD"],
        "max_notional_per_action": "100.00",
        "max_notional_per_day": "250.00",
        "require_approval_at": "50.00",
        "denied_assets": [],
    }
    policy.update(overrides)
    return policy


def test_allows_action_that_satisfies_policy():
    result = evaluate_action(_draft(), _policy(), now=NOW, executed_notional_today="10")

    assert result.decision is GuardDecision.ALLOW
    assert result.reason_codes == ("POLICY_MATCH",)
    assert result.policy_matched is True


def test_requires_approval_at_configured_threshold():
    result = evaluate_action(
        _draft(notional={"amount": "50", "currency": "USD"}),
        _policy(),
        now=NOW,
    )

    assert result.decision is GuardDecision.REQUIRE_APPROVAL
    assert "APPROVAL_THRESHOLD" in result.reason_codes


@pytest.mark.parametrize(
    ("draft_patch", "policy_patch", "reason"),
    [
        ({"action_type": "withdraw"}, {}, "ACTION_TYPE_DENIED"),
        ({"asset_class": "derivative"}, {}, "ASSET_CLASS_DENIED"),
        ({"asset_id": "BLOCKED"}, {"denied_assets": ["BLOCKED"]}, "ASSET_DENIED"),
        ({"destination": {"venue_id": "venue-x"}}, {}, "VENUE_DENIED"),
        ({"notional": {"amount": "101", "currency": "USD"}}, {}, "ACTION_LIMIT_EXCEEDED"),
    ],
)
def test_denies_policy_violations(draft_patch, policy_patch, reason):
    result = evaluate_action(
        _draft(**draft_patch), _policy(**policy_patch), now=NOW
    )

    assert result.decision is GuardDecision.DENY
    assert reason in result.reason_codes


def test_denies_when_daily_limit_would_be_exceeded():
    result = evaluate_action(
        _draft(notional={"amount": "25", "currency": "USD"}),
        _policy(),
        now=NOW,
        executed_notional_today="230",
    )

    assert result.decision is GuardDecision.DENY
    assert "DAILY_LIMIT_EXCEEDED" in result.reason_codes


def test_denies_expired_action():
    result = evaluate_action(
        _draft(expires_at=(NOW - timedelta(seconds=1)).isoformat()),
        _policy(),
        now=NOW,
    )

    assert result.decision is GuardDecision.DENY
    assert "ACTION_EXPIRED" in result.reason_codes


@pytest.mark.parametrize("forbidden_key", ["private_key", "seed_phrase", "calldata", "serialized_transaction"])
def test_denies_transaction_or_signing_material_anywhere(forbidden_key):
    result = evaluate_action(
        _draft(context={"nested": {forbidden_key: "must-not-be-accepted"}}),
        _policy(),
        now=NOW,
    )

    assert result.decision is GuardDecision.DENY
    assert "FORBIDDEN_TRANSACTION_MATERIAL" in result.reason_codes


def test_invalid_numeric_input_fails_closed():
    result = evaluate_action(
        _draft(notional={"amount": "NaN", "currency": "USD"}),
        _policy(),
        now=NOW,
    )

    assert result.decision is GuardDecision.DENY
    assert "INVALID_ACTION_DRAFT" in result.reason_codes
