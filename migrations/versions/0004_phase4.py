"""Phase 4 — advisors lookup table + request_queue idempotency for institutional writes.

Revision ID: 0004_phase4
Revises: 0003_phase3
Create Date: 2026-06-17

Hand-authored. Additive on existing tables; one new table: advisors (spec §4).

New table:
  advisors — F4 escalation routing reference (NOT an auth principal; no role, no login).

Additive columns on request_queue (F1-F3 institutional writes):
  request_queue.target          — text; program_id / course_code the request targets.
  request_queue.idempotency_key — text; blocks duplicate filings.

New index:
  uq_request_queue_pending — partial unique on (tenant_id, student_id, type, target)
                             WHERE status='pending'; one PENDING filing per (student, type, target).

Constraint change:
  Drop ck_outbox_kind. Phase 3 repurposed outbox.kind to carry the canonical event
  type (enrollment_confirmation, graduation_application, …) alongside the added
  outbox.event_type column; the old kind IN ('email','notification') check no longer
  holds. The publisher reads event_type; kind is now a free-form event name.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_phase4"
down_revision: str | None = "0003_phase3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.TIMESTAMP(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")

_NEW_TENANT_OWNED: tuple[str, ...] = ("advisors",)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. advisors — F4 routing reference (spec §4). No role, no auth.
    # ------------------------------------------------------------------
    op.create_table(
        "advisors",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("email", sa.Text, nullable=False),
        # Routing by program/department; null = catch-all advisor for the tenant.
        sa.Column("program", sa.Text, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_advisors_tenant_id", "advisors", ["tenant_id"])
    op.create_index("ix_advisors_program", "advisors", ["tenant_id", "program"])

    # ------------------------------------------------------------------
    # 2. request_queue — institutional-write idempotency (spec §4)
    # ------------------------------------------------------------------
    op.add_column("request_queue", sa.Column("target", sa.Text, nullable=True))
    op.add_column("request_queue", sa.Column("idempotency_key", sa.Text, nullable=True))
    # One PENDING filing per (tenant, student, type, target). Re-file allowed only
    # after the prior request leaves 'pending' (approved/rejected) — spec D6.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_request_queue_pending "
        "ON request_queue (tenant_id, student_id, type, target) "
        "WHERE status = 'pending'"
    )

    # ------------------------------------------------------------------
    # 3. outbox — drop the stale channel-only check (see module docstring)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE outbox DROP CONSTRAINT IF EXISTS ck_outbox_kind")

    # ------------------------------------------------------------------
    # 4. RLS on the new tenant-owned table
    # ------------------------------------------------------------------
    for table in _NEW_TENANT_OWNED:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """
        )


def downgrade() -> None:
    for table in reversed(_NEW_TENANT_OWNED):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("DROP INDEX IF EXISTS uq_request_queue_pending")
    op.drop_column("request_queue", "idempotency_key")
    op.drop_column("request_queue", "target")

    # Restore the original Phase-0 channel check.
    op.execute(
        "ALTER TABLE outbox ADD CONSTRAINT ck_outbox_kind CHECK (kind IN ('email','notification'))"
    )
