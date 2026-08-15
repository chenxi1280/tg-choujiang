"""add user_group_message_totals

Revision ID: 3f1d9a7c21b4
Revises: c6d4e8f1a205
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f1d9a7c21b4"
down_revision: str | Sequence[str] | None = "c6d4e8f1a205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_group_message_totals",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("last_content_hash", sa.String(length=64), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_group_message_totals")),
        sa.UniqueConstraint(
            "user_id",
            "chat_id",
            name=op.f("uq_user_group_message_totals_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_user_group_message_totals_chat_id"),
        "user_group_message_totals",
        ["chat_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_group_message_totals_chat_id"),
        table_name="user_group_message_totals",
    )
    op.drop_table("user_group_message_totals")
