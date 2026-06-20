"""SECURITY DEFINER helpers for platform operator aggregate counts.

Revision ID: 0008_platform_count_functions
Revises: 0007_fix_users_rls
Create Date: 2026-06-19

Problem:
  The platform operator's list_tenants endpoint needs to count students and
  tenant_admin users per tenant.  These tables have FORCE RLS; querying them
  without an app.tenant_id context causes
      "invalid input syntax for type uuid: ''"
  because the RLS policy casts current_setting('app.tenant_id', true) to uuid
  unconditionally.

Solution:
  SECURITY DEFINER functions owned by postgres (which has BYPASSRLS) return
  aggregate counts without touching tenant content.  The platform operator
  never sees row-level data — only counts.
"""

from __future__ import annotations

from alembic import op


def upgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION platform_count_students(p_tenant_id uuid)
        RETURNS bigint
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT count(*) FROM students WHERE tenant_id = p_tenant_id;
        $$
    """)
    op.execute("ALTER FUNCTION platform_count_students(uuid) OWNER TO postgres")
    op.execute("GRANT EXECUTE ON FUNCTION platform_count_students(uuid) TO keel_app")

    op.execute("""
        CREATE OR REPLACE FUNCTION platform_count_admins(p_tenant_id uuid)
        RETURNS bigint
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT count(*) FROM users
          WHERE tenant_id = p_tenant_id AND role IN ('tenant_admin', 'admin');
        $$
    """)
    op.execute("ALTER FUNCTION platform_count_admins(uuid) OWNER TO postgres")
    op.execute("GRANT EXECUTE ON FUNCTION platform_count_admins(uuid) TO keel_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS platform_count_students(uuid)")
    op.execute("DROP FUNCTION IF EXISTS platform_count_admins(uuid)")
