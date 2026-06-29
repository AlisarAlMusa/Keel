"""Drop the now-unused portal SECURITY DEFINER functions (shrink BYPASSRLS surface).

Revision ID: 0012_drop_portal_definer
Revises: 0011_definer_bypassrls
Create Date: 2026-06-24

Why:
  ``portal_find_by_email``, ``portal_find_student`` and ``portal_list_students``
  were SECURITY DEFINER (owned by ``keel_definer``, BYPASSRLS) so the portal could
  read across tenants before a session existed. But a portal instance ALWAYS knows
  its own tenant (``PORTAL_TENANT``), and the ``tenants`` table has no RLS — so the
  portal server now resolves its tenant_id and runs ordinary RLS-scoped queries
  inside ``withTenantTx`` instead (see frontend/portal/server/index.cjs, D-R-015).

  With no remaining callers, these three functions are dead bypass surface. Dropping
  them shrinks the SECURITY DEFINER / BYPASSRLS set to only the functions that are
  *genuinely* cross-tenant: the Keel-console login lookup (``keel_find_user_by_email``
  — tenant unknown until the user is found), the operator aggregates
  (``platform_count_*`` / ``platform_usage_summary`` — cross-tenant by role), and the
  startup bootstrap reads (``tenant_names_all`` / ``widget_config_all`` /
  ``widget_origins_all``). ``portal_find_student`` was never called at all.

Safety:
  ``DROP FUNCTION IF EXISTS`` is idempotent. The portal refactor ships in the same
  change, so there is no window where a caller references a dropped function.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_drop_portal_definer"
down_revision: str | None = "0011_definer_bypassrls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS portal_find_by_email(text)")
    op.execute("DROP FUNCTION IF EXISTS portal_find_student(uuid)")
    op.execute("DROP FUNCTION IF EXISTS portal_list_students()")


def downgrade() -> None:
    # Intentional no-op: recreating these SECURITY DEFINER functions would re-expand
    # the BYPASSRLS surface this migration deliberately shrank, and the portal no
    # longer calls them (it reads RLS-scoped). Mirrors 0011's downgrade rationale.
    pass
