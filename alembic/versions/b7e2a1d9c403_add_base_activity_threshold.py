"""Separate consecutive daily speaking qualification from bonus tickets."""

import sqlalchemy as sa
from alembic import op

revision = "b7e2a1d9c403"
down_revision = "9a7d3c5e8b20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("base_qualified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "base_activity_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "campaign_id",
            sa.Uuid(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            sa.Uuid(),
            sa.ForeignKey("participants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.String(64)),
        sa.Column("source_update_id", sa.BigInteger()),
        sa.UniqueConstraint("campaign_id", "chat_id", "message_id"),
    )
    op.create_index(
        "ix_base_activity_participant_date",
        "base_activity_events",
        ["participant_id", "chat_id", "local_date"],
    )


def downgrade() -> None:
    op.drop_table("base_activity_events")
    op.drop_column("participants", "base_qualified")
