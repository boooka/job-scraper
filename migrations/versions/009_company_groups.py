"""Add company_groups canonical table and companies.group_id FK.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-08 00:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_groups",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("normalized_key", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
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
        sa.UniqueConstraint("normalized_key", name="uq_company_group_normalized_key"),
    )
    op.create_index("ix_company_groups_name", "company_groups", ["name"])

    op.add_column("companies", sa.Column("group_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_companies_group_id",
        "companies",
        "company_groups",
        ["group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_companies_group_id", "companies", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_companies_group_id", table_name="companies")
    op.drop_constraint("fk_companies_group_id", "companies", type_="foreignkey")
    op.drop_column("companies", "group_id")
    op.drop_index("ix_company_groups_name", table_name="company_groups")
    op.drop_table("company_groups")
