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
