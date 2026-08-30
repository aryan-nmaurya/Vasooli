-- A database role the application can use without bypassing row-level security.
--
-- RLS has three silent bypasses, and the deployment hit all three: policies existed,
-- FORCE was not set, and the connecting role was both the table owner and a
-- superuser. Forcing RLS fixes the owner case; nothing fixes the superuser case,
-- because superusers bypass RLS unconditionally by design. The app therefore needs a
-- role that is neither.
--
-- Run as the owning role (vasooli), once per database, AFTER migrations:
--   docker compose -f docker-compose.prod.yml exec -T db \
--     psql -U vasooli -d vasooli -v app_password="'<strong-password>'" \
--     -f /path/to/create_app_role.sql
--
-- Then point DATABASE_URL at vasooli_app and restart. Migrations keep running as the
-- owner: DDL is not the app's job, and an app role that can ALTER TABLE could switch
-- its own policies off.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vasooli_app') THEN
        CREATE ROLE vasooli_app LOGIN;
    END IF;
END
$$;

ALTER ROLE vasooli_app WITH PASSWORD :app_password;
ALTER ROLE vasooli_app WITH NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE vasooli TO vasooli_app;
GRANT USAGE ON SCHEMA public TO vasooli_app;

-- Data access only. No DDL, so the app cannot disable the policies that constrain it.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vasooli_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vasooli_app;

-- Tables created by future migrations must be reachable too, or the next deploy
-- fails with a permission error at runtime rather than at migration time.
-- No FOR ROLE: these apply to whoever runs this script, which must be the role
-- that owns the tables and runs migrations. Naming it explicitly would break on
-- any deployment whose owner is not called 'vasooli'.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vasooli_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO vasooli_app;

SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'vasooli_app';
