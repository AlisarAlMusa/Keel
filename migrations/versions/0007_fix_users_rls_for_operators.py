"""Fix users RLS policy to support platform_operator rows (tenant_id IS NULL).

Revision ID: 0007_fix_users_rls
Revises: 0006_platform_operator
Create Date: 2026-06-19

Problem fixed:
  The original tenant_isolation policy on users cast current_setting('app.tenant_id')
  to uuid unconditionally.  When app.tenant_id is not set (empty string) — e.g. in
  the auth login flow before a session exists — the cast fails with
  "invalid input syntax for type uuid: ''".

  Additionally, platform_operator rows have tenant_id IS NULL.  The old policy
  (tenant_id = <uuid>) never matches NULL, making operators invisible and
  un-insertable via the keel_app role.

New policy logic:
  - No tenant context (app.tenant_id = ''): only operator rows (tenant_id IS NULL)
    pass.  This is exactly what the auth login needs — no tenant rows leak.
  - Tenant context set: normal tenant_id filter applies.  Operators are invisible.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_fix_users_rls"
down_revision: str | None = "0006_platform_operator"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Transfer auth function ownership to postgres so SECURITY DEFINER bypasses RLS.
    # keel_app has NOBYPASSRLS, so functions owned by keel_app can't read across tenants.
    op.execute("ALTER FUNCTION keel_find_user_by_email(text) OWNER TO postgres")
    op.execute("ALTER FUNCTION portal_find_by_email(text) OWNER TO postgres")

    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON users
          USING (
            CASE
              WHEN COALESCE(current_setting('app.tenant_id', true), '') = ''
                THEN (tenant_id IS NULL)
              ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
            END
          )
          WITH CHECK (
            CASE
              WHEN COALESCE(current_setting('app.tenant_id', true), '') = ''
                THEN (tenant_id IS NULL)
              ELSE tenant_id = current_setting('app.tenant_id', true)::uuid
            END
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON users")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON users
          USING  (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
