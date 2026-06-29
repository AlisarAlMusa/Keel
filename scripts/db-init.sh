#!/bin/bash
# Postgres init script (runs once, as the superuser, on first DB creation).
#
# CRITICAL for tenant isolation: the application connects as `keel_app`, which
# MUST be a NON-superuser with NOBYPASSRLS so Row-Level Security is enforced.
# A superuser (or BYPASSRLS role) would silently skip every RLS policy.
#
# Extensions are created here by the superuser so the later (non-superuser)
# migration only needs `CREATE EXTENSION IF NOT EXISTS` (a no-op).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS pgcrypto;
    CREATE EXTENSION IF NOT EXISTS vector;

    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keel_app') THEN
            CREATE ROLE keel_app LOGIN PASSWORD '${KEEL_APP_PASSWORD}'
                NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
        END IF;
    END
    \$\$;

    -- keel_definer: the SECURITY DEFINER owner for the handful of vetted
    -- cross-tenant functions (pre-session login lookup, platform aggregate
    -- counts, portal/widget bootstrap reads). These MUST read before any
    -- app.tenant_id is set, so their owner needs BYPASSRLS. keel_app itself
    -- stays NOBYPASSRLS — isolation is intact for every normal query. This role
    -- cannot log in and is granted to keel_app only as MEMBERSHIP, so migrations
    -- (run as keel_app) can own/replace these functions without a superuser.
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'keel_definer') THEN
            CREATE ROLE keel_definer NOLOGIN
                NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
        END IF;
    END
    \$\$;
    GRANT keel_definer TO keel_app;

    -- Every table keel_app creates later (in migrations) auto-grants SELECT to
    -- keel_definer, so the SECURITY DEFINER cross-tenant functions (which run as
    -- keel_definer) can read them. BYPASSRLS governs row visibility; this grants
    -- the separate table-level SELECT privilege. Read-only by design.
    ALTER DEFAULT PRIVILEGES FOR ROLE keel_app IN SCHEMA public
        GRANT SELECT ON TABLES TO keel_definer;
    GRANT USAGE ON SCHEMA public TO keel_definer;

    -- keel_app owns the public schema so migrations it runs create tables it
    -- owns; combined with FORCE ROW LEVEL SECURITY, policies apply to it too.
    ALTER SCHEMA public OWNER TO keel_app;
    GRANT ALL ON SCHEMA public TO keel_app;
    GRANT ALL ON DATABASE ${POSTGRES_DB} TO keel_app;
EOSQL

# Separate database for the MLflow tracking server (metadata + model registry).
# Not tenant data — owned by the superuser, no RLS. CREATE DATABASE can't run in
# a DO block or transaction, so use \gexec to make it idempotent.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE mlflow'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mlflow')\gexec
EOSQL
