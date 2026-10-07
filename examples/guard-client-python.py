"""Minimal CryptoMind Guard client. It never builds or submits a transaction."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

base_url = os.environ["CRYPTOMIND_GUARD_URL"].rstrip("/")
api_key = os.environ["CRYPTOMIND_GUARD_API_KEY"]
now = datetime.now(timezone.utc)
action_id = f"action-{uuid.uuid4()}"

draft = {
    "external_action_id": action_id,
    "subject_ref": "pseudonymous-subject",
    "action_type": "trade",
    "asset_class": "crypto",
    "asset_id": "TON",
    "side": "buy",
    "notional": {"amount": "25.00", "currency": "USD"},
    "destination": {"venue_id": "executor-a"},
    "requested_at": now.isoformat(),
    "expires_at": (now + timedelta(minutes=5)).isoformat(),
}

response = httpx.post(
    f"{base_url}/v1/guard/evaluate",
    headers={
        "X-Guard-API-Key": api_key,
        "Idempotency-Key": action_id,
    },
    json=draft,
    timeout=10,
)
response.raise_for_status()
decision = response.json()
print(decision["decision"], decision["reason_codes"], decision["decision_id"])
