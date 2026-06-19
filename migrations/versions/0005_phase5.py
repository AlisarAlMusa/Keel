"""Phase 5 — widget auth, usage tracking, widget config.

Revision ID: 0005_phase5
Revises: 0004_phase4
Create Date: 2026-06-19

Additive changes only:

  enrollments.source — text column ('keel'|'manual'|'sis'), default 'sis'.
    Marks which writes the Keel agent made (drives the "via Keel" badge).

  usage_event — LLM/embedding cost tracking (Keel-domain, RLS-scoped).
    Columns: id, tenant_id, kind ('llm'|'embedding'), tokens int, cost_estimate
    numeric, created_at.

  widget_config — per-tenant Keel agent configuration (Keel-domain, RLS-scoped).
    Columns: id, tenant_id, persona text, persona_name text,
    allowed_origins jsonb, enabled_tools jsonb, updated_at.
    One row per tenant (partial unique index on tenant_id).

Safety rails are NOT stored here — they are hardcoded in infra/guardrails.py.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_phase5"
down_revision: str | None = "0004_phase4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.TIMESTAMP(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")

_NEW_TENANT_OWNED: tuple[str, ...] = ("usage_event", "widget_config")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. enrollments.source — provenance ("via Keel" badge)
    # ------------------------------------------------------------------
    op.add_column(
        "enrollments",
        sa.Column("source", sa.Text, nullable=False, server_default=sa.text("'sis'")),
    )
    op.execute(
        "ALTER TABLE enrollments ADD CONSTRAINT ck_enrollment_source "
        "CHECK (source IN ('keel', 'manual', 'sis'))"
    )

    # ------------------------------------------------------------------
    # 2. usage_event — LLM/embedding cost tracking
    # ------------------------------------------------------------------
    op.create_table(
        "usage_event",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text, nullable=False),  # 'llm' | 'embedding'
        sa.Column("tokens", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "cost_estimate",
            sa.Numeric(12, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_usage_event_tenant_id", "usage_event", ["tenant_id"])
    op.create_index("ix_usage_event_created_at", "usage_event", ["created_at"])
    op.execute(
        "ALTER TABLE usage_event ADD CONSTRAINT ck_usage_event_kind "
        "CHECK (kind IN ('llm', 'embedding'))"
    )

    # ------------------------------------------------------------------
    # 3. widget_config — per-tenant Keel agent configuration
    # ------------------------------------------------------------------
    op.create_table(
        "widget_config",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # one config row per tenant
        ),
        sa.Column(
            "persona",
            sa.Text,
            nullable=False,
            server_default=sa.text(
                "'You are Keel, a helpful AI academic advisor. "
                "You help students plan their courses thoughtfully and safely.'"
            ),
        ),
        sa.Column("persona_name", sa.Text, nullable=False, server_default=sa.text("'Keel'")),
        # List of allowed origins for the CORS/origin check.
        sa.Column(
            "allowed_origins",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Subset of tool names the tenant has enabled (empty = all defaults).
        sa.Column(
            "enabled_tools",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("updated_at", _TS, nullable=False, server_default=_NOW),
    )
    op.create_index("ix_widget_config_tenant_id", "widget_config", ["tenant_id"])

    # ------------------------------------------------------------------
    # 4. RLS on new Keel-domain tables
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


    # ------------------------------------------------------------------
    # 5. SECURITY DEFINER function for portal login tenant resolution
    # ------------------------------------------------------------------
    # The portal server connects as keel_app (NOBYPASSRLS). For the login
    # step, it needs to look up a student's tenant_id before a session
    # exists — i.e., before it can set app.tenant_id for RLS.
    # This function runs as its owner (postgres superuser = BYPASSRLS) so
    # keel_app can resolve the tenant without bypassing RLS everywhere.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION portal_find_student(p_student_id UUID)
        RETURNS TABLE(out_student_id UUID, out_tenant_id UUID)
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT id, tenant_id FROM students WHERE id = p_student_id;
        $$
        """
    )
    # Grant execute to the application role.
    op.execute("GRANT EXECUTE ON FUNCTION portal_find_student(UUID) TO keel_app")

    # portal_list_students: returns all students with user + tenant info for the
    # portal demo switcher (SSO stand-in). Same SECURITY DEFINER pattern.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION portal_list_students()
        RETURNS TABLE(
          student_id UUID,
          tenant_id UUID,
          tenant_slug TEXT,
          tenant_name TEXT,
          email TEXT,
          display_name TEXT,
          program_code TEXT,
          has_hold BOOLEAN
        )
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT
            s.id,
            s.tenant_id,
            t.slug,
            t.name,
            u.email,
            u.display_name,
            s.program_code,
            s.has_hold
          FROM students s
          JOIN users u ON u.id = s.user_id
          JOIN tenants t ON t.id = s.tenant_id
          ORDER BY t.slug, u.display_name
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION portal_list_students() TO keel_app")

    # widget_origins_all: startup helper for keel-api to read all tenants'
    # allowed_origins without setting app.tenant_id (SECURITY DEFINER bypasses
    # RLS so keel_app — NOBYPASSRLS — can warm the in-process origins cache).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION widget_origins_all()
        RETURNS TABLE(tenant_id UUID, allowed_origins JSONB)
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT tenant_id, allowed_origins FROM widget_config
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION widget_origins_all() TO keel_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS widget_origins_all()")
    op.execute("DROP FUNCTION IF EXISTS portal_list_students()")
    op.execute("DROP FUNCTION IF EXISTS portal_find_student(UUID)")

    for table in reversed(_NEW_TENANT_OWNED):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS ck_enrollment_source")
    op.drop_column("enrollments", "source")
