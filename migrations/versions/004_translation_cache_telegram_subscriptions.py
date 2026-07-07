"""Add translation_cache and telegram_subscriptions tables.

Revision ID: 0004
Revises: 0003
Create Date: 2024-01-04 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "translation_cache",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("source_text", sa.Text, nullable=False),
        sa.Column("translated_text", sa.Text, nullable=False),
        sa.Column("translator", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("language", "text_hash", name="uq_translation_cache_language_hash"),
    )
    op.create_index("ix_translation_cache_language", "translation_cache", ["language"])

    op.create_table(
        "telegram_subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("username", sa.String(255)),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_telegram_subscriptions_user",
        "telegram_subscriptions",
        ["telegram_user_id"],
    )
    op.create_index("ix_telegram_subscriptions_chat", "telegram_subscriptions", ["chat_id"])
    op.create_index(
        "ix_telegram_subscriptions_is_active",
        "telegram_subscriptions",
        ["is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_subscriptions_is_active", table_name="telegram_subscriptions")
    op.drop_index("ix_telegram_subscriptions_chat", table_name="telegram_subscriptions")
    op.drop_index("ix_telegram_subscriptions_user", table_name="telegram_subscriptions")
    op.drop_table("telegram_subscriptions")

    op.drop_index("ix_translation_cache_language", table_name="translation_cache")
    op.drop_table("translation_cache")
