"""Phase 5 addendum — platform operator role + portal user auth.

Revision ID: 0006_platform_operator
Revises: 0005_phase5
Create Date: 2026-06-19

Changes (all additive):

1. users.tenant_id: make nullable — platform_operator carries no tenant scope.
2. users.hashed_password: add nullable TEXT — only tenant_admin + platform_operator set this.
3. ck_users_role: replace old ('admin','student') with expanded set including
   'tenant_admin' and 'platform_operator'.
4. ck_operator_no_tenant: (role <> 'platform_operator') OR (tenant_id IS NULL).
5. ck_admin_has_tenant:   (role <> 'tenant_admin') OR (tenant_id IS NOT NULL).
6. uq_users_operator_email: partial unique index on email WHERE tenant_id IS NULL
   (handles uniqueness among operators since NULL != NULL breaks the existing
   uq_users_tenant_email unique constraint).
7. platform_audit: platform-domain table, NOT RLS-scoped.  Survives tenant erase.
8. platform_usage_summary(text): SECURITY DEFINER aggregate — no content, counts only.
9. portal_user: SIS/portal-domain table, RLS by tenant_id.  Holds portal login creds.
10. portal_find_by_email(): SECURITY DEFINER — lets portal server look up a user by
    email before a session exists (pre-tenant bootstrap query).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_platform_operator"
down_revision: str | None = "0005_phase5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB
_TS = sa.TIMESTAMP(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. users.tenant_id → nullable  (operators carry no tenant scope)
    # ------------------------------------------------------------------
    op.alter_column("users", "tenant_id", nullable=True)

    # ------------------------------------------------------------------
    # 2. users.hashed_password
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("hashed_password", sa.Text, nullable=True))

    # ------------------------------------------------------------------
    # 3. ck_users_role — expand to include new roles
    # ------------------------------------------------------------------
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role "
        "CHECK (role IN ('admin', 'student', 'tenant_admin', 'platform_operator'))"
    )

    # ------------------------------------------------------------------
    # 4. Operator isolation check constraints
    # ------------------------------------------------------------------
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_operator_no_tenant "
        "CHECK ((role <> 'platform_operator') OR (tenant_id IS NULL))"
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_admin_has_tenant "
        "CHECK ((role <> 'tenant_admin') OR (tenant_id IS NOT NULL))"
    )

    # ------------------------------------------------------------------
    # 5. Partial unique index for operator emails
    #    The existing uq_users_tenant_email (tenant_id, email) does not enforce
    #    uniqueness when tenant_id IS NULL because NULL != NULL.
    # ------------------------------------------------------------------
    op.execute(
        "CREATE UNIQUE INDEX uq_users_operator_email ON users(email) WHERE tenant_id IS NULL"
    )

    # ------------------------------------------------------------------
    # 6. platform_audit — NOT RLS-scoped; survives tenant erase by design
    # ------------------------------------------------------------------
    op.create_table(
        "platform_audit",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "actor_user_id",
            _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column(
            "target_tenant_id",
            _UUID,
            # SET NULL so the audit row survives after erase removes the tenant row
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detail", _JSONB, nullable=True),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint(
            "action IN ('provision', 'suspend', 'unsuspend', 'erase')",
            name="ck_platform_audit_action",
        ),
    )
    op.create_index("ix_platform_audit_created_at", "platform_audit", ["created_at"])
    op.create_index("ix_platform_audit_target_tenant_id", "platform_audit", ["target_tenant_id"])
    # platform_audit is intentionally NOT added to TENANT_OWNED_TABLES — no RLS.

    # ------------------------------------------------------------------
    # 7. platform_usage_summary(p_period) — SECURITY DEFINER aggregate
    #    Returns per-tenant grouped numbers; no content rows are returned.
    #    Operator endpoint calls ONLY this function — never raw usage_event.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION platform_usage_summary(p_period text)
        RETURNS TABLE(tenant_id uuid, kind text, calls bigint, tokens bigint, cost numeric)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT
            ue.tenant_id,
            ue.kind,
            count(*)::bigint,
            coalesce(sum(ue.tokens), 0)::bigint,
            coalesce(sum(ue.cost_estimate), 0)
          FROM usage_event ue
          WHERE ue.created_at >= now() - (
            CASE p_period
              WHEN 'month' THEN interval '30 days'
              WHEN 'day'   THEN interval  '1 day'
              ELSE              interval  '7 days'
            END
          )
          GROUP BY ue.tenant_id, ue.kind
          ORDER BY ue.tenant_id, ue.kind
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION platform_usage_summary(text) TO keel_app")

    # ------------------------------------------------------------------
    # 8. portal_user — SIS/portal-domain; RLS by tenant_id
    #    Holds portal login credentials (students + registrar).
    #    Completely separate from Keel's users table.
    # ------------------------------------------------------------------
    op.create_table(
        "portal_user",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),  # 'student' | 'registrar'
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text, nullable=False),
        # For students: FK to SIS students row; for registrar: NULL
        sa.Column(
            "student_id",
            _UUID,
            sa.ForeignKey("students.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", _TS, nullable=False, server_default=_NOW),
        sa.CheckConstraint("role IN ('student', 'registrar')", name="ck_portal_user_role"),
    )
    op.create_index("ix_portal_user_tenant_id", "portal_user", ["tenant_id"])
    op.create_index("ix_portal_user_email", "portal_user", ["email"])

    op.execute("ALTER TABLE portal_user ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_user FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON portal_user
          USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )

    # ------------------------------------------------------------------
    # 9. portal_find_by_email() — SECURITY DEFINER lookup
    #    Lets the portal server (keel_app, NOBYPASSRLS) resolve a portal_user
    #    row by email before a session exists (pre-tenant bootstrap query).
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION portal_find_by_email(p_email text)
        RETURNS TABLE(
          user_id      uuid,
          tenant_id    uuid,
          role         text,
          hashed_password text,
          student_id   uuid
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT id, tenant_id, role, hashed_password, student_id
          FROM portal_user
          WHERE email = p_email
          LIMIT 1
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION portal_find_by_email(text) TO keel_app")

    # ------------------------------------------------------------------
    # 10. keel_find_user_by_email() — SECURITY DEFINER lookup
    #     Lets the API look up a Keel user (admin/operator) by email
    #     for the email+password login flow, bypassing RLS on users.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION keel_find_user_by_email(p_email text)
        RETURNS TABLE(
          user_id         uuid,
          tenant_id       uuid,
          role            text,
          hashed_password text
        )
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT id, tenant_id, role, hashed_password
          FROM users
          WHERE email = p_email
          AND role IN ('tenant_admin', 'platform_operator')
          LIMIT 1
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION keel_find_user_by_email(text) TO keel_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS keel_find_user_by_email(text)")
    op.execute("DROP FUNCTION IF EXISTS portal_find_by_email(text)")
    op.execute("DROP TABLE IF EXISTS portal_user CASCADE")
    op.execute("DROP FUNCTION IF EXISTS platform_usage_summary(text)")
    op.execute("DROP TABLE IF EXISTS platform_audit CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_users_operator_email")
    op.drop_constraint("ck_admin_has_tenant", "users", type_="check")
    op.drop_constraint("ck_operator_no_tenant", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_role CHECK (role IN ('admin', 'student'))"
    )
    op.drop_column("users", "hashed_password")
    op.alter_column("users", "tenant_id", nullable=False)
