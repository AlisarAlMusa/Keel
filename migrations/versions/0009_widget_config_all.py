"""SECURITY DEFINER helpers for widget persona and tenant name lookups.

Revision ID: 0009_widget_config_all
Revises: 0008_platform_count_functions
Create Date: 2026-06-20

Adds two startup helpers that keel_app (NOBYPASSRLS) can call to warm
in-process caches without needing SET LOCAL row_security = OFF:

  widget_config_all()  — returns (tenant_id, persona_name, allowed_origins)
  tenant_names_all()   — returns (id, slug, name) from tenants table
"""

from alembic import op

revision = "0009_widget_config_all"
down_revision = "0008_platform_count_functions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION widget_config_all()
        RETURNS TABLE(tenant_id UUID, persona_name TEXT, allowed_origins JSONB)
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT tenant_id, persona_name, allowed_origins FROM widget_config
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION widget_config_all() TO keel_app")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION tenant_names_all()
        RETURNS TABLE(id UUID, slug TEXT, name TEXT)
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT id, slug, name FROM tenants
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION tenant_names_all() TO keel_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS widget_config_all()")
    op.execute("DROP FUNCTION IF EXISTS tenant_names_all()")
