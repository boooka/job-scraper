"""Add cities dictionary and vacancies.city_id FK.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-06 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name_en", sa.String(255), nullable=False),
        sa.Column("name_translated", sa.String(255)),
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
        sa.UniqueConstraint("name_en", name="uq_city_name_en"),
    )
    op.create_index("ix_cities_name_translated", "cities", ["name_translated"])

    op.add_column("vacancies", sa.Column("city_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_vacancies_city_id",
        "vacancies",
        "cities",
        ["city_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_vacancies_city_id", "vacancies", ["city_id"])


def downgrade() -> None:
    op.drop_index("ix_vacancies_city_id", table_name="vacancies")
    op.drop_constraint("fk_vacancies_city_id", "vacancies", type_="foreignkey")
    op.drop_column("vacancies", "city_id")
    op.drop_index("ix_cities_name_translated", table_name="cities")
    op.drop_table("cities")
