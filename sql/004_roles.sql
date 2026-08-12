-- GridCast — 004 Roles and grants
--
-- This file is where the append-only guarantee stops being a promise and
-- becomes a property. Append-only enforced by code convention is a claim about
-- author discipline; enforced by a database grant, it is a property of the
-- system that holds even if the author is careless, or dishonest, or replaced.
--
-- Two roles:
--   gridcast_app       — the pipeline. Writes landing, marts and the register.
--                        Can INSERT into the register. Cannot UPDATE or DELETE it.
--   gridcast_readonly  — the serving API. SELECT only, everywhere.
--
-- Idempotent: safe to re-run. Uses DO blocks because CREATE ROLE has no
-- IF NOT EXISTS in PostgreSQL.

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gridcast_app') THEN
        CREATE ROLE gridcast_app;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gridcast_readonly') THEN
        CREATE ROLE gridcast_readonly;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- gridcast_app — pipeline
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA landing, staging, marts, register TO gridcast_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA landing TO gridcast_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA marts   TO gridcast_app;
GRANT SELECT                        ON ALL TABLES IN SCHEMA staging TO gridcast_app;
GRANT USAGE  ON ALL SEQUENCES IN SCHEMA landing  TO gridcast_app;
GRANT USAGE  ON ALL SEQUENCES IN SCHEMA register TO gridcast_app;

-- The register: INSERT only. This is the whole point of the file.
GRANT SELECT, INSERT ON register.reg_forecast_point TO gridcast_app;
GRANT SELECT, INSERT ON register.reg_forecast_score TO gridcast_app;
GRANT SELECT, INSERT ON register.reg_forecast_seal  TO gridcast_app;
GRANT SELECT, INSERT ON register.reg_seal_audit     TO gridcast_app;

REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_forecast_point FROM gridcast_app;
REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_forecast_score FROM gridcast_app;
REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_forecast_seal  FROM gridcast_app;
REVOKE UPDATE, DELETE, TRUNCATE ON register.reg_seal_audit     FROM gridcast_app;

-- ---------------------------------------------------------------------------
-- gridcast_readonly — serving API
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA landing, staging, marts, register TO gridcast_readonly;

GRANT SELECT ON ALL TABLES IN SCHEMA landing  TO gridcast_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA staging  TO gridcast_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA marts    TO gridcast_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA register TO gridcast_readonly;

-- ---------------------------------------------------------------------------
-- Defaults for objects dbt creates later
--
-- Without these, every new dbt model would be invisible to the API until
-- someone remembered to grant it — a failure that looks like a bug in the
-- frontend and gets debugged in the wrong place.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA staging
    GRANT SELECT ON TABLES TO gridcast_readonly, gridcast_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts
    GRANT SELECT ON TABLES TO gridcast_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gridcast_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA landing
    GRANT SELECT ON TABLES TO gridcast_readonly;
