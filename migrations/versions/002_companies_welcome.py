"""Add companies table, migrate company data from vacancies, add welcome_ukraine and page_html.

Revision ID: 0002
Revises: 0001
Create Date: 2024-01-02 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Create companies table ─────────────────────────────────────
    op.create_table(
        "companies",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("employee_count", sa.Integer),
        sa.Column("country", sa.String(100)),
        sa.Column("office_address", sa.Text),
        sa.Column("contact_person", sa.String(255)),
        sa.Column("extra", JSONB),
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
        sa.UniqueConstraint("source", "external_id", name="uq_company_source_external_id"),
    )
    op.create_index("ix_companies_name", "companies", ["name"])

    # ── 2. Add new columns to vacancies ──────────────────────────────
    op.add_column(
        "vacancies",
        sa.Column("company_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vacancies",
        sa.Column("company_name", sa.String(255), nullable=True),
    )
    op.add_column(
        "vacancies",
        sa.Column(
            "welcome_ukraine",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "vacancies",
        sa.Column("salary_type", sa.String(20), nullable=True),
    )
    op.add_column(
        "vacancies",
        sa.Column("page_html", sa.Text, nullable=True),
    )

    # ── 3. Migrate existing company strings → companies table ─────────
    # Insert one company row per unique (source, company) pair,
    # using a generated external_id since we have no original one.
    op.execute(
        """
        INSERT INTO companies (id, source, external_id, name, created_at, updated_at)
        SELECT
            gen_random_uuid(),
            source,
            md5(source || '::' || company),   -- stable synthetic external_id
            company,
            now(),
            now()
        FROM (
            SELECT DISTINCT source, company
            FROM vacancies
            WHERE company IS NOT NULL AND company <> ''
        ) AS unique_companies
        ON CONFLICT (source, external_id) DO NOTHING
        """
    )

    # Back-fill company_name from old company column
    op.execute(
        """
        UPDATE vacancies
        SET company_name = company
        WHERE company IS NOT NULL
        """
    )

    # Back-fill company_id FK
    op.execute(
        """
        UPDATE vacancies v
        SET company_id = c.id
        FROM companies c
        WHERE c.source = v.source
          AND c.external_id = md5(v.source || '::' || v.company)
          AND v.company IS NOT NULL
        """
    )

    # ── 4. Create FK + indexes ────────────────────────────────────────
    op.create_foreign_key(
        "fk_vacancies_company_id",
        "vacancies",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_vacancies_company_id", "vacancies", ["company_id"])
    op.create_index("ix_vacancies_welcome_ukraine", "vacancies", ["welcome_ukraine"])

    # ── 5. Drop old denorm company column ────────────────────────────
    # Keep it commented out until you've verified the migration in staging.
    # op.drop_column("vacancies", "company")


def downgrade() -> None:
    # Restore company column if it was dropped
    # op.add_column("vacancies", sa.Column("company", sa.String(255)))
    # op.execute("UPDATE vacancies SET company = company_name")

    op.drop_constraint("fk_vacancies_company_id", "vacancies", type_="foreignkey")
    op.drop_index("ix_vacancies_company_id", table_name="vacancies")
    op.drop_index("ix_vacancies_welcome_ukraine", table_name="vacancies")
    op.drop_column("vacancies", "company_id")
    op.drop_column("vacancies", "company_name")
    op.drop_column("vacancies", "welcome_ukraine")
    op.drop_column("vacancies", "salary_type")
    op.drop_column("vacancies", "page_html")
    op.drop_index("ix_companies_name", table_name="companies")
    op.drop_table("companies")