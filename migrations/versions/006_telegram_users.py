"""Add telegram_users table.

Revision ID: 0006
Revises: 0005
Create Date: 2024-01-06 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("username", sa.String(255)),
        sa.Column("first_name", sa.String(255)),
        sa.Column("last_name", sa.String(255)),
        sa.Column("language_code", sa.String(32)),
        sa.Column("is_bot", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_premium", sa.Boolean),
        sa.Column("last_chat_id", sa.BigInteger),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_telegram_users_username", "telegram_users", ["username"])
    op.create_index("ix_telegram_users_last_seen_at", "telegram_users", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_users_last_seen_at", table_name="telegram_users")
    op.drop_index("ix_telegram_users_username", table_name="telegram_users")
    op.drop_table("telegram_users")
