"""Add schedules table for admin-managed cron schedules.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-10 00:00:00

The table is created empty; the scheduler process seeds the known job rows
from settings (real .env cron values) on first start, then the DB becomes the
source of truth and the Django admin edits it live.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # Stable job identifier matching the scheduler's JOB_REGISTRY keys
        # (e.g. "cvbankas", "translations", "daily_report").
        sa.Column("job_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        # 5-field cron expression (minute hour day month day_of_week).
        sa.Column("cron", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        # Set by the admin "Run now" action; scheduler triggers the job once and
        # treats already-past values on startup as handled.
        sa.Column("run_now_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("job_id", name="uq_schedules_job_id"),
    )


def downgrade() -> None:
    op.drop_table("schedules")
