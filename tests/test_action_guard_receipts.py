from core.action_guard.receipts import create_signed_receipt, verify_signed_receipt

SECRET = "test-secret-with-enough-entropy-for-receipts"


def _payload():
    return {
        "decision_id": "dec_123",
        "client_id": "client_123",
        "external_action_id": "act_123",
        "subject_ref": "customer@example.test",
        "decision": "ALLOW",
        "reason_codes": ["POLICY_MATCH"],
        "action_summary": {
            "asset_id": "TON",
            "amount": "25.00",
            "currency": "USD",
            "api_key": "never-store-this",
        },
    }


def test_receipt_is_deterministic_and_verifiable():
    first = create_signed_receipt(_payload(), secret=SECRET, previous_hash="0" * 64)
    second = create_signed_receipt(_payload(), secret=SECRET, previous_hash="0" * 64)

    assert first == second
    assert verify_signed_receipt(first, secret=SECRET) is True


def test_receipt_redacts_subject_and_secret_fields():
    receipt = create_signed_receipt(_payload(), secret=SECRET)

    assert "subject_ref" not in receipt["payload"]
    assert len(receipt["payload"]["subject_ref_hash"]) == 64
    assert receipt["payload"]["action_summary"]["api_key"] == "[REDACTED]"


def test_tampered_receipt_fails_verification():
    receipt = create_signed_receipt(_payload(), secret=SECRET)
    receipt["payload"]["decision"] = "DENY"

    assert verify_signed_receipt(receipt, secret=SECRET) is False


def test_previous_hash_is_covered_by_signature():
    receipt = create_signed_receipt(_payload(), secret=SECRET, previous_hash="a" * 64)
    receipt["previous_hash"] = "b" * 64

    assert verify_signed_receipt(receipt, secret=SECRET) is False
