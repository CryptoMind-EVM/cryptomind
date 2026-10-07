// Minimal CryptoMind Guard client. It never builds or submits a transaction.
import crypto from 'node:crypto';

const baseUrl = process.env.CRYPTOMIND_GUARD_URL.replace(/\/$/, '');
const apiKey = process.env.CRYPTOMIND_GUARD_API_KEY;
const actionId = `action-${crypto.randomUUID()}`;
const now = new Date();

const response = await fetch(`${baseUrl}/v1/guard/evaluate`, {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-Guard-API-Key': apiKey,
        'Idempotency-Key': actionId,
    },
    body: JSON.stringify({
        external_action_id: actionId,
        subject_ref: 'pseudonymous-subject',
        action_type: 'trade',
        asset_class: 'crypto',
        asset_id: 'TON',
        side: 'buy',
        notional: { amount: '25.00', currency: 'USD' },
        destination: { venue_id: 'executor-a' },
        requested_at: now.toISOString(),
        expires_at: new Date(now.getTime() + 5 * 60_000).toISOString(),
    }),
});

if (!response.ok) throw new Error(`Guard request failed: ${response.status}`);
const result = await response.json();
console.log(result.decision, result.reason_codes, result.decision_id);
