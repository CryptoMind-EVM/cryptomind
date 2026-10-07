from __future__ import annotations

import copy
import hashlib
import hmac
import json
from typing import Any, Mapping

_REDACT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "cookie",
        "jwt",
        "mnemonic",
        "order_token",
        "password",
        "private_key",
        "seed_phrase",
        "signature",
        "token",
    }
)
_DOMAIN = b"cryptomind-action-receipt-v1"


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _receipt_key(secret: str) -> bytes:
    if not isinstance(secret, str) or len(secret) < 16:
        raise ValueError("receipt secret is missing or too short")
    return hmac.new(secret.encode("utf-8"), _DOMAIN, hashlib.sha256).digest()


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            if key.lower() in _REDACT_KEYS:
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = _redact(child)
        return cleaned
    if isinstance(value, list):
        return [_redact(child) for child in value]
    if isinstance(value, tuple):
        return [_redact(child) for child in value]
    return copy.deepcopy(value)


def _prepare_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = _redact(payload)
    subject_ref = cleaned.pop("subject_ref", None)
    if subject_ref is not None:
        cleaned["subject_ref_hash"] = hashlib.sha256(
            str(subject_ref).encode("utf-8")
        ).hexdigest()
    return cleaned


def create_signed_receipt(
    payload: Mapping[str, Any],
    *,
    secret: str,
    previous_hash: str = "",
) -> dict[str, Any]:
    body = {
        "version": "cm-guard-receipt-v1",
        "previous_hash": previous_hash,
        "payload": _prepare_payload(payload),
    }
    receipt_hash = hashlib.sha256(_canonical(body)).hexdigest()
    signature = hmac.new(
        _receipt_key(secret), receipt_hash.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return {**body, "receipt_hash": receipt_hash, "signature": signature}


def verify_signed_receipt(receipt: Mapping[str, Any], *, secret: str) -> bool:
    try:
        body = {
            "version": receipt["version"],
            "previous_hash": receipt["previous_hash"],
            "payload": receipt["payload"],
        }
        actual_hash = hashlib.sha256(_canonical(body)).hexdigest()
        expected_hash = str(receipt["receipt_hash"])
        if not hmac.compare_digest(actual_hash, expected_hash):
            return False
        expected_signature = hmac.new(
            _receipt_key(secret), expected_hash.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(str(receipt["signature"]), expected_signature)
    except (KeyError, TypeError, ValueError):
        return False
