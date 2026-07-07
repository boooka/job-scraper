-- Runs once at PostgreSQL cluster initialisation (before Alembic migrations),
-- so it may only contain statements that do NOT depend on application tables.
-- Monitoring views (v_latest_scrape_runs, v_active_vacancies) live in the
-- Alembic migration 0008_monitoring_views, which runs after the schema exists.

-- Enable UUID / crypto helpers
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
