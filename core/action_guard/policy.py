from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .types import GuardDecision, GuardEvaluation

_FORBIDDEN_MATERIAL_KEYS = frozenset(
    {
        "private_key",
        "privatekey",
        "seed",
        "seed_phrase",
        "mnemonic",
        "signature",
        "signed_transaction",
        "serialized_transaction",
        "raw_transaction",
        "calldata",
        "boc",
        "wallet_secret",
    }
)


def _deny(*reasons: str, policy_matched: bool = False) -> GuardEvaluation:
    return GuardEvaluation(GuardDecision.DENY, tuple(reasons), policy_matched)


def _contains_forbidden_material(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _FORBIDDEN_MATERIAL_KEYS:
                return True
            if _contains_forbidden_material(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_material(child) for child in value)
    return False


def _decimal(value: Any) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed < 0:
        raise InvalidOperation
    return parsed


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _normalized_set(policy: Mapping[str, Any], key: str) -> set[str]:
    raw = policy.get(key, [])
    if not isinstance(raw, (list, tuple, set)):
        raise ValueError(f"{key} must be a list")
    return {str(item).strip().lower() for item in raw}


def evaluate_action(
    draft: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    now: datetime | None = None,
    executed_notional_today: Any = "0",
) -> GuardEvaluation:
    """Evaluate an action draft without selecting, recommending or executing a trade.

    Invalid data always returns ``DENY``. The function performs no network, database,
    wallet, transaction-building or LLM work, making a decision reproducible from the
    supplied draft, policy version and usage total.
    """

    if _contains_forbidden_material(draft):
        return _deny("FORBIDDEN_TRANSACTION_MATERIAL")

    try:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        requested_at = _timestamp(draft.get("requested_at"))
        expires_at = _timestamp(draft.get("expires_at"))
        if expires_at <= current_time:
            return _deny("ACTION_EXPIRED")
        if requested_at > expires_at:
            return _deny("INVALID_ACTION_DRAFT")
        if requested_at > current_time + timedelta(minutes=5):
            return _deny("INVALID_ACTION_DRAFT")

        notional = draft.get("notional")
        destination = draft.get("destination")
        if not isinstance(notional, Mapping) or not isinstance(destination, Mapping):
            return _deny("INVALID_ACTION_DRAFT")

        amount = _decimal(notional.get("amount"))
        used_today = _decimal(executed_notional_today)
        if amount <= 0 or not math.isfinite(float(amount)):
            return _deny("INVALID_ACTION_DRAFT")

        action_type = str(draft.get("action_type", "")).strip().lower()
        asset_class = str(draft.get("asset_class", "")).strip().lower()
        asset_id = str(draft.get("asset_id", "")).strip().lower()
        currency = str(notional.get("currency", "")).strip().lower()
        venue_id = str(destination.get("venue_id", "")).strip().lower()
        if not all((action_type, asset_class, asset_id, currency, venue_id)):
            return _deny("INVALID_ACTION_DRAFT")

        allowed_actions = _normalized_set(policy, "allowed_action_types")
        allowed_classes = _normalized_set(policy, "allowed_asset_classes")
        allowed_venues = _normalized_set(policy, "allowed_venues")
        allowed_currencies = _normalized_set(policy, "allowed_currencies")
        denied_assets = _normalized_set(policy, "denied_assets")

        if action_type not in allowed_actions:
            return _deny("ACTION_TYPE_DENIED", policy_matched=True)
        if asset_class not in allowed_classes:
            return _deny("ASSET_CLASS_DENIED", policy_matched=True)
        if asset_id in denied_assets:
            return _deny("ASSET_DENIED", policy_matched=True)
        if venue_id not in allowed_venues:
            return _deny("VENUE_DENIED", policy_matched=True)
        if currency not in allowed_currencies:
            return _deny("CURRENCY_DENIED", policy_matched=True)

        per_action = _decimal(policy.get("max_notional_per_action"))
        per_day = _decimal(policy.get("max_notional_per_day"))
        approval_at = _decimal(policy.get("require_approval_at"))
        if amount > per_action:
            return _deny("ACTION_LIMIT_EXCEEDED", policy_matched=True)
        if used_today + amount > per_day:
            return _deny("DAILY_LIMIT_EXCEEDED", policy_matched=True)
        if amount >= approval_at:
            return GuardEvaluation(
                GuardDecision.REQUIRE_APPROVAL,
                ("APPROVAL_THRESHOLD",),
                True,
            )
        return GuardEvaluation(GuardDecision.ALLOW, ("POLICY_MATCH",), True)
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return _deny("INVALID_ACTION_DRAFT")
