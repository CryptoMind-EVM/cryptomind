"""consent_gate 單元測試：同意等級規則 + 同意足跡 canonical hash。

全離線——純函式。
"""

from __future__ import annotations

from safety_kernel.consent_gate import (
    consent_payload_hash,
    requires_consent,
)


class TestRequiresConsent:
    def test_orange_and_red_require_consent(self):
        assert requires_consent("orange")
        assert requires_consent("red")

    def test_green_yellow_no_consent(self):
        assert not requires_consent("green")
        assert not requires_consent("yellow")

    def test_block_is_not_consentable(self):
        # 硬擋 = 沒有同意選項。
        assert not requires_consent("block")

    def test_unknown_level_fails_closed_to_consent(self):
        # 未知等級 → 保守要求同意。
        assert requires_consent("mystery")

    def test_constant_matches_risk_tiers(self):
        # orange/red 是 risk_tiers 中 needs_consent=True 的等級。
        from safety_kernel.risk_tiers import assess_risk

        for pi in (3.0, 4.5, 5.0, 10.0):
            risk = assess_risk(pi, None)
            assert requires_consent(risk.level)


class TestConsentPayloadHash:
    def _payload(self, **kw):
        base = {
            "q": "quote-abc",
            "u": "user-1",
            "i": "1000000000",
            "a": "native",
            "o": "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs",
            "m": "1391317",
            "pi": "1.2",
            "rl": "yellow",
            "kv": "0.1.0",
            "e": 9999999999,  # 時效欄位不該影響 hash
            "w": "UQ...wallet",  # 內部欄位不該影響 hash
        }
        base.update(kw)
        return base

    def test_hash_is_sha256_hex(self):
        h = consent_payload_hash(self._payload())
        assert len(h) == 64
        int(h, 16)  # 是合法 hex

    def test_same_facts_same_hash(self):
        a = consent_payload_hash(self._payload())
        b = consent_payload_hash(self._payload())
        assert a == b

    def test_key_order_does_not_matter(self):
        import json

        p = self._payload()
        reordered = json.loads(json.dumps(p))  # 同 dict，不同插入序
        assert consent_payload_hash(p) == consent_payload_hash(reordered)

    def test_changed_fact_changes_hash(self):
        a = consent_payload_hash(self._payload())
        b = consent_payload_hash(self._payload(i="2000000000"))  # 金額變了
        assert a != b

    def test_changed_kernel_version_changes_hash(self):
        a = consent_payload_hash(self._payload())
        b = consent_payload_hash(self._payload(kv="0.2.0"))
        assert a != b

    def test_internal_fields_ignored(self):
        # wallet / expiry 不屬於「同意事實」，改動不影響 hash。
        a = consent_payload_hash(self._payload())
        b = consent_payload_hash(self._payload(w="UQ...other", e=123))
        assert a == b

    def test_missing_facts_treated_as_empty(self):
        # 舊版 token（無 pi/rl/kv 欄位）→ 空字串，hash 仍穩定可算。
        p = {k: v for k, v in self._payload().items() if k not in ("pi", "rl", "kv")}
        h = consent_payload_hash(p)
        assert len(h) == 64
