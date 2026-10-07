"""add Action Guard SaaS tables

Revision ID: c022_action_guard
Revises: c021_drop_daily_board
"""

from alembic import op


revision = "c022_action_guard"
down_revision = "c021_drop_daily_board"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE guard_clients (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'suspended')),
            environment TEXT NOT NULL DEFAULT 'live'
                CHECK (environment IN ('test', 'live')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_guard_clients_owner ON guard_clients(owner_user_id);

        CREATE TABLE guard_api_keys (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES guard_clients(id) ON DELETE CASCADE,
            key_prefix TEXT NOT NULL,
            key_hash TEXT NOT NULL UNIQUE,
            scopes TEXT[] NOT NULL,
            expires_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_guard_api_keys_client ON guard_api_keys(client_id);

        CREATE TABLE guard_policies (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES guard_clients(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            document JSONB NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT FALSE,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_guard_policy_version UNIQUE(client_id, version)
        );
        CREATE UNIQUE INDEX uq_guard_policy_active
            ON guard_policies(client_id) WHERE is_active;

        CREATE TABLE guard_decisions (
            id TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES guard_clients(id) ON DELETE CASCADE,
            external_action_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            subject_ref_hash TEXT NOT NULL,
            action_summary JSONB NOT NULL,
            policy_id TEXT NOT NULL REFERENCES guard_policies(id),
            decision TEXT NOT NULL CHECK (decision IN ('ALLOW', 'DENY', 'REQUIRE_APPROVAL')),
            reason_codes TEXT[] NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('allowed', 'denied', 'pending_approval', 'approved', 'executed', 'failed', 'expired')),
            approval_expires_at TIMESTAMPTZ,
            outcome JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_guard_external_action UNIQUE(client_id, external_action_id),
            CONSTRAINT uq_guard_idempotency_key UNIQUE(client_id, idempotency_key)
        );
        CREATE INDEX idx_guard_decisions_client_created
            ON guard_decisions(client_id, created_at DESC);

        CREATE TABLE action_receipts (
            id TEXT PRIMARY KEY,
            client_id TEXT REFERENCES guard_clients(id),
            user_id TEXT,
            decision_id TEXT REFERENCES guard_decisions(id),
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            previous_hash TEXT NOT NULL DEFAULT '',
            receipt_hash TEXT NOT NULL UNIQUE,
            signature TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_action_receipts_client_created
            ON action_receipts(client_id, created_at DESC);
        CREATE INDEX idx_action_receipts_user_created
            ON action_receipts(user_id, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS action_receipts")
    op.execute("DROP TABLE IF EXISTS guard_decisions")
    op.execute("DROP TABLE IF EXISTS guard_policies")
    op.execute("DROP TABLE IF EXISTS guard_api_keys")
    op.execute("DROP TABLE IF EXISTS guard_clients")
