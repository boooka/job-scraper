"""Initial schema: vacancies, vacancy_changes, scrape_runs.

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vacancies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(255)),
        sa.Column("location", sa.String(255)),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("salary_min", sa.Integer),
        sa.Column("salary_max", sa.Integer),
        sa.Column("salary_currency", sa.String(10)),
        sa.Column("salary_period", sa.String(20)),
        sa.Column("extra", JSONB),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_vacancy_source_external_id"),
    )
    op.create_index("ix_vacancies_source", "vacancies", ["source"])
    op.create_index("ix_vacancies_is_active", "vacancies", ["is_active"])
    op.create_index("ix_vacancies_last_seen_at", "vacancies", ["last_seen_at"])

    op.create_table(
        "vacancy_changes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey("vacancies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
    )
    op.create_index("ix_vacancy_changes_vacancy_id", "vacancy_changes", ["vacancy_id"])
    op.create_index("ix_vacancy_changes_changed_at", "vacancy_changes", ["changed_at"])

    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), server_default="running"),
        sa.Column("error_message", sa.Text),
        sa.Column("vacancies_found", sa.Integer, server_default="0"),
        sa.Column("new_count", sa.Integer, server_default="0"),
        sa.Column("changed_count", sa.Integer, server_default="0"),
        sa.Column("deactivated_count", sa.Integer, server_default="0"),
    )
    op.create_index("ix_scrape_runs_source", "scrape_runs", ["source"])
    op.create_index("ix_scrape_runs_started_at", "scrape_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("scrape_runs")
    op.drop_table("vacancy_changes")
    op.drop_table("vacancies")
