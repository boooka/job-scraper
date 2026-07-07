"""Create monitoring views (v_latest_scrape_runs, v_active_vacancies).

These previously lived in scripts/init_db.sql, which Postgres runs at cluster
initialisation — *before* Alembic creates the tables — so the views were never
created (and v_active_vacancies referenced a non-existent `company` column).
Defining them here guarantees they exist after the schema does.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-07 00:00:00
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW v_latest_scrape_runs AS
        SELECT DISTINCT ON (source)
            id, source, started_at, finished_at, status,
            vacancies_found, new_count, changed_count, deactivated_count,
            error_message
        FROM scrape_runs
        ORDER BY source, started_at DESC;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_active_vacancies AS
        SELECT
            v.id,
            v.source,
            v.title,
            v.company_name,
            v.location,
            c.name_en        AS city_name_en,
            c.name_translated AS city_name_translated,
            v.salary_min,
            v.salary_max,
            v.salary_currency,
            v.url,
            v.first_seen_at,
            v.last_seen_at,
            COUNT(vc.id) AS change_count
        FROM vacancies v
        LEFT JOIN cities c ON c.id = v.city_id
        LEFT JOIN vacancy_changes vc ON vc.vacancy_id = v.id
        WHERE v.is_active = true
        GROUP BY v.id, c.name_en, c.name_translated;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_active_vacancies;")
    op.execute("DROP VIEW IF EXISTS v_latest_scrape_runs;")
