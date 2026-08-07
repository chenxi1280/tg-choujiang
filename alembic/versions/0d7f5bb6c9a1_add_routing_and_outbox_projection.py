"""add durable update routing and replayable outbox projection

Revision ID: 0d7f5bb6c9a1
Revises: 992c1dd08da2
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0d7f5bb6c9a1"
down_revision: str | Sequence[str] | None = "992c1dd08da2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telegram_updates", sa.Column("routing_chat_id", sa.BigInteger()))
    op.add_column("telegram_updates", sa.Column("routing_campaign_id", sa.String(length=36)))
    op.create_index(
        op.f("ix_telegram_updates_routing_chat_id"),
        "telegram_updates",
        ["routing_chat_id"],
    )
    op.create_index(
        op.f("ix_telegram_updates_routing_campaign_id"),
        "telegram_updates",
        ["routing_campaign_id"],
    )
    op.add_column("telegram_outbox", sa.Column("encrypted_remote_secret", sa.LargeBinary()))
    op.add_column("telegram_outbox", sa.Column("projected_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE delivery_attempts "
        "SET remote_result = remote_result - 'invite_link' "
        "WHERE remote_result ? 'invite_link'"
    )


def downgrade() -> None:
    op.drop_column("telegram_outbox", "projected_at")
    op.drop_column("telegram_outbox", "encrypted_remote_secret")
    op.drop_index(op.f("ix_telegram_updates_routing_campaign_id"), table_name="telegram_updates")
    op.drop_index(op.f("ix_telegram_updates_routing_chat_id"), table_name="telegram_updates")
    op.drop_column("telegram_updates", "routing_campaign_id")
    op.drop_column("telegram_updates", "routing_chat_id")
