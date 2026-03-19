"""Add Telegram delivery dedup table and FTS indexes.

Revision ID: 0005
Revises: 0004
Create Date: 2024-01-05 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_subscription_deliveries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "subscription_id",
            sa.BigInteger,
            sa.ForeignKey("telegram_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vacancy_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vacancies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "subscription_id",
            "vacancy_id",
            name="uq_telegram_delivery_subscription_vacancy",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_subscription_id",
        "telegram_subscription_deliveries",
        ["subscription_id"],
    )
    op.create_index(
        "ix_telegram_delivery_vacancy_id",
        "telegram_subscription_deliveries",
        ["vacancy_id"],
    )

    op.execute(
        """
        CREATE INDEX ix_vacancies_fts
        ON vacancies
        USING gin (
            to_tsvector(
                'simple',
                coalesce(title, '') || ' ' || regexp_replace(coalesce(description, ''), '<[^>]+>', ' ', 'g')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_vacancy_translations_fts_all_lang
        ON vacancy_translations
        USING gin (
            to_tsvector(
                'simple',
                coalesce(title_translated, '') || ' ' || regexp_replace(coalesce(description_translated, ''), '<[^>]+>', ' ', 'g')
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vacancy_translations_fts_all_lang")
    op.execute("DROP INDEX IF EXISTS ix_vacancies_fts")
    op.drop_index(
        "ix_telegram_delivery_vacancy_id",
        table_name="telegram_subscription_deliveries",
    )
    op.drop_index(
        "ix_telegram_delivery_subscription_id",
        table_name="telegram_subscription_deliveries",
    )
    op.drop_table("telegram_subscription_deliveries")
