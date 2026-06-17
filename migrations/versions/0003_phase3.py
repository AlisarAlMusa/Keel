"""Phase 3 — action table, outbox publisher columns, waitlist MVP columns, plan active flag.

Revision ID: 0003_phase3
Revises: 0002_phase2
Create Date: 2026-06-16

Hand-authored. Additive on existing tables; new table: actions.

New table:
  actions — the resumable-agent action pattern (spec §2).

Additive columns:
  outbox.processed      — bool; set true AFTER RQ job confirms success.
  outbox.attempts       — int;  incremented per publish attempt.
  outbox.event_type     — text; canonical event name (replaces 'kind' semantics for
                          new rows; old rows keep kind != null for backwards read).
  waitlist.auto_enroll  — bool; delegated-consent flag (spec §1a).
  waitlist.status       — text; waiting|fulfilled|failed|left.
  plans.is_active       — bool; one-active-plan partial unique index.
  plans.catalog_version — text; for load-revalidation on catalog change.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_phase3"
down_revision: str | None = "0002_phase2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.TIMESTAMP(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")

_NEW_TENANT_OWNED: tuple[str, ...] = ("actions",)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. actions table — the heart of Phase 3 (spec §1.1 / plan.md §1.1)
    # ------------------------------------------------------------------
    op.create_table(
        "actions",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            _UUID,
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # LangGraph checkpoint thread to resume on approve.
        # Written at stage time, read at approve time — NEVER supplied via request.
        sa.Column("thread_id", sa.Text, nullable=False),
        # enrollment | waitlist_join | waitlist_leave
        # (Phase 5 adds: petition | major_change | graduation_app)
        sa.Column("type", sa.Text, nullable=False),
        # FROZEN approved payload. Execute node reads this; ignores LLM args after resume.
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # pending → approved → executed | rejected | failed | expired
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.Column("decided_at", _TS, nullable=True),
        sa.Column(
            "audit_ref",
            sa.BigInteger,
            sa.ForeignKey("audit_log.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "type IN ('enrollment','waitlist_join','waitlist_leave')",
            name="ck_actions_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','executed','rejected','failed','expired')",
            name="ck_actions_status",
        ),
    )
    op.create_index("ix_actions_tenant_id", "actions", ["tenant_id"])
    op.create_index("ix_actions_student_id", "actions", ["student_id"])
    op.create_index(
        "ix_actions_status_pending",
        "actions",
        ["tenant_id", "status"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # 2. outbox — add publisher/dedup columns
    # ------------------------------------------------------------------
    op.add_column(
        "outbox",
        sa.Column("event_type", sa.Text, nullable=True),  # null on old rows that used 'kind'
    )
    op.add_column(
        "outbox",
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "outbox",
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
    )
    op.create_index(
        "ix_outbox_unprocessed",
        "outbox",
        ["tenant_id", "created_at"],
        postgresql_where=sa.text("processed = false"),
    )

    # ------------------------------------------------------------------
    # 3. waitlist — MVP columns for delegated-consent auto-enroll
    # ------------------------------------------------------------------
    op.add_column(
        "waitlist",
        sa.Column("auto_enroll", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "waitlist",
        # waiting | fulfilled | failed | left
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'waiting'")),
    )
    op.add_constraint = lambda: None  # silence mypy; raw DDL below
    op.execute(
        "ALTER TABLE waitlist ADD CONSTRAINT ck_waitlist_status "
        "CHECK (status IN ('waiting','fulfilled','failed','left'))"
    )

    # ------------------------------------------------------------------
    # 4. plans — one-active-plan support
    # ------------------------------------------------------------------
    op.add_column(
        "plans",
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "plans",
        sa.Column("catalog_version", sa.Text, nullable=True),
    )
    # Partial unique index: only one active plan per student.
    op.execute(
        "CREATE UNIQUE INDEX uq_plans_one_active ON plans (student_id) WHERE is_active = true"
    )

    # ------------------------------------------------------------------
    # 5. RLS on new tenant-owned table
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
    # Drop new table
    for table in reversed(_NEW_TENANT_OWNED):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Drop new plan columns
    op.execute("DROP INDEX IF EXISTS uq_plans_one_active")
    op.drop_column("plans", "catalog_version")
    op.drop_column("plans", "is_active")

    # Drop new waitlist columns
    op.execute("ALTER TABLE waitlist DROP CONSTRAINT IF EXISTS ck_waitlist_status")
    op.drop_column("waitlist", "status")
    op.drop_column("waitlist", "auto_enroll")

    # Drop new outbox columns
    op.execute("DROP INDEX IF EXISTS ix_outbox_unprocessed")
    op.drop_column("outbox", "attempts")
    op.drop_column("outbox", "processed")
    op.drop_column("outbox", "event_type")
