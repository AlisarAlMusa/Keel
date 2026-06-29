"""SECURITY DEFINER helpers for widget persona and tenant name lookups.

Revision ID: 0009_widget_config_all
Revises: 0008_platform_count_functions
Create Date: 2026-06-20

Adds two startup helpers that keel_app (NOBYPASSRLS) can call to warm
in-process caches without needing SET LOCAL row_security = OFF:

  widget_config_all()  — returns (tenant_id, persona_name, persona, allowed_origins)
  tenant_names_all()   — returns (id, slug, name) from tenants table

The widget_config_all() result includes ``persona`` (the per-tenant system-prompt
prefix) because the app (main._load_widget_config) reads all four columns to warm
the persona-prompt cache. An earlier version of this migration omitted ``persona``;
it is restored here so a clean ``alembic upgrade head`` produces the function shape
the application actually queries.
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
        RETURNS TABLE(tenant_id UUID, persona_name TEXT, persona TEXT, allowed_origins JSONB)
        LANGUAGE SQL
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT tenant_id, persona_name, persona, allowed_origins FROM widget_config
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
