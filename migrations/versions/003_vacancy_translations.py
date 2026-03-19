"""Add vacancy_translations table for RU (and other language) translations.

Revision ID: 0003
Revises: 0002
Create Date: 2024-01-03 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vacancy_translations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "vacancy_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # ISO 639-1 code: "ru", "en", "lt", etc.
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("title_translated", sa.String(500)),
        sa.Column("description_translated", sa.Text),
        sa.Column(
            "translated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Which service produced this translation: "deepl" / "google" / "llm" / "manual"
        sa.Column("translator", sa.String(50)),
        sa.UniqueConstraint(
            "vacancy_id", "language", name="uq_translation_vacancy_language"
        ),
    )
    op.create_index(
        "ix_vacancy_translations_vacancy_id", "vacancy_translations", ["vacancy_id"]
    )
    op.create_index(
        "ix_vacancy_translations_language", "vacancy_translations", ["language"]
    )

    # Full-text search index on Russian translations for fast search
    op.execute(
        """
        CREATE INDEX ix_vacancy_translations_fts_ru
        ON vacancy_translations
        USING gin(
            to_tsvector(
                'russian',
                coalesce(title_translated, '') || ' ' || coalesce(description_translated, '')
            )
        )
        WHERE language = 'ru'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_vacancy_translations_fts_ru", table_name="vacancy_translations")
    op.drop_index("ix_vacancy_translations_language", table_name="vacancy_translations")
    op.drop_index("ix_vacancy_translations_vacancy_id", table_name="vacancy_translations")
    op.drop_table("vacancy_translations")