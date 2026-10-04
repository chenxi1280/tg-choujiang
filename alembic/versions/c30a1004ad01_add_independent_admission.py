"""Independent V3 admission and explicit participation intent."""

import sqlalchemy as sa
from alembic import op

revision = "c30a1004ad01"
down_revision = "b7e2a1d9c403"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participants",
        sa.Column("admission_qualified", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    for name in ("registration_intent_at", "counting_started_at"):
        op.add_column("participants", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("participants", sa.Column("counting_started_update_id", sa.BigInteger()))
    op.add_column("base_activity_events", sa.Column("participation_group_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_base_activity_participation_group",
        "base_activity_events",
        "campaign_participation_groups",
        ["participation_group_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_table(
        "admission_progress",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        _identity("campaign_id", "campaigns.id"),
        _identity("participant_id", "participants.id"),
        _identity("participation_group_id", "campaign_participation_groups.id"),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("qualified", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("source_update_id", sa.BigInteger()),
        sa.UniqueConstraint("participant_id", "participation_group_id", "mode"),
    )
    _create_message_events()


def _create_message_events() -> None:
    op.create_table(
        "message_total_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("source_update_id", sa.BigInteger()),
        sa.UniqueConstraint("chat_id", "message_id"),
    )


def _identity(name: str, target: str) -> sa.Column:
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(target, ondelete="CASCADE"), nullable=False)


def downgrade() -> None:
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM campaigns WHERE rules->>'participation_rules_version' = 'v3.0'"
        )
    )
    if count:
        raise RuntimeError("V3.0 campaign facts exist; destructive admission downgrade refused")
    op.drop_table("message_total_events")
    op.drop_table("admission_progress")
    op.drop_constraint(
        "fk_base_activity_participation_group", "base_activity_events", type_="foreignkey"
    )
    op.drop_column("base_activity_events", "participation_group_id")
    for name in (
        "counting_started_update_id",
        "counting_started_at",
        "registration_intent_at",
        "admission_qualified",
    ):
        op.drop_column("participants", name)
