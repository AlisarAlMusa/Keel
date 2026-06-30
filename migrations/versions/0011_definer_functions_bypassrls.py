"""Own every SECURITY DEFINER cross-tenant function by keel_definer (BYPASSRLS).

Revision ID: 0011_definer_bypassrls
Revises: 0010_institutional_action_types
Create Date: 2026-06-23

Problem fixed (auth outage):
  The SECURITY DEFINER helpers — keel_find_user_by_email, portal_find_by_email,
  portal_find_student, portal_list_students, platform_count_*, platform_usage_summary,
  tenant_names_all, widget_config_all, widget_origins_all — must read ACROSS
  tenants before any app.tenant_id session context exists (you cannot know a
  user's tenant until you have found the user). SECURITY DEFINER runs a function
  with the privileges of its OWNER, so the owner must have BYPASSRLS for the
  function to see tenant rows pre-session.

  A prior change set these functions' owner to keel_app, which is deliberately
  NOBYPASSRLS (the isolation guarantee). With keel_app as owner, RLS still
  applied inside the functions: with no tenant set, the users policy returned
  only rows where tenant_id IS NULL — so ONLY the platform_operator could log
  in, and every tenant_admin / student / portal lookup silently returned 0 rows.

Fix:
  keel_definer (NOLOGIN, BYPASSRLS) is created in scripts/db-init.sh by the
  superuser at bootstrap and GRANTed to keel_app as membership. This migration
  (run as keel_app) reassigns every SECURITY DEFINER function in `public` that
  keel_app currently owns to keel_definer. keel_app stays NOBYPASSRLS for all
  normal queries; only these vetted functions run with bypass.

  We fail LOUD if keel_definer is missing — a silent no-op here is exactly how
  the outage went unnoticed before. keel_app cannot CREATE ROLE ... BYPASSRLS
  (NOCREATEROLE), so the role must come from db-init.sh.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_definer_bypassrls"
down_revision: str | None = "0010_institutional_action_types"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            r record;
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'keel_definer') THEN
                RAISE EXCEPTION
                    'keel_definer role is missing. scripts/db-init.sh must create it '
                    '(NOLOGIN BYPASSRLS) and GRANT it to keel_app. Without it the '
                    'SECURITY DEFINER cross-tenant functions run under keel_app '
                    '(NOBYPASSRLS) and silently return 0 rows pre-session, breaking '
                    'login and portal/widget bootstrap.';
            END IF;

            -- ALTER FUNCTION ... OWNER TO keel_definer requires the *new owner*
            -- to hold CREATE on the function's schema. keel_definer only has
            -- USAGE, so without this the reassignment below fails with
            -- "permission denied for schema public". keel_app owns the public
            -- schema (db-init.sh), so it may grant CREATE here.
            GRANT CREATE ON SCHEMA public TO keel_definer;

            -- Reassign only the functions keel_app currently owns. On a clean
            -- build every SECURITY DEFINER function in public is created by the
            -- migrations (i.e. owned by keel_app), so this covers all of them.
            FOR r IN
                SELECT p.oid::regprocedure AS sig
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                  AND p.prosecdef
                  AND pg_get_userbyid(p.proowner) = 'keel_app'
            LOOP
                EXECUTE format('ALTER FUNCTION %s OWNER TO keel_definer', r.sig);
            END LOOP;
        END
        $$
        """
    )

    # BYPASSRLS only controls row VISIBILITY; table-level SELECT is a separate
    # privilege. keel_definer owns no tables (keel_app does), so it must be
    # granted read access for the SECURITY DEFINER functions (which now run as
    # keel_definer) to read users/students/tenants/etc. These functions are
    # read-only lookups/aggregates — SELECT is all they need.
    op.execute("GRANT USAGE ON SCHEMA public TO keel_definer")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO keel_definer")
    # Cover tables created by later migrations too.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE keel_app IN SCHEMA public "
        "GRANT SELECT ON TABLES TO keel_definer"
    )


def downgrade() -> None:
    # Ownership reassignment is not safely reversible without reintroducing the
    # auth outage (keel_app as owner = NOBYPASSRLS = 0 rows pre-session). Leave
    # the functions owned by keel_definer; this downgrade is an intentional no-op.
    pass
