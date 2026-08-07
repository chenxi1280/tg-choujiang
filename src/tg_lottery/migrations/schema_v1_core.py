import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


def step_10_create_index_uq_campaigns_group_nonterminal() -> None:
    op.create_index(
        "uq_campaigns_group_nonterminal",
        "campaigns",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('drawn', 'archived', 'cancelled')"),
    )


def step_11_create_table_delivery_attempts() -> None:
    op.create_table(
        "delivery_attempts",
        sa.Column("outbox_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("telegram_method", sa.String(length=80), nullable=False),
        sa.Column(
            "certainty",
            sa.Enum(
                "confirmed",
                "explicit_rejection",
                "unknown",
                name="delivery_certainty",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_description", sa.Text(), nullable=True),
        sa.Column(
            "remote_result",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["outbox_id"],
            ["telegram_outbox.id"],
            name=op.f("fk_delivery_attempts_outbox_id_telegram_outbox"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_attempts")),
    )


def step_12_create_table_group_admins() -> None:
    op.create_table(
        "group_admins",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_group_admins_group_id_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_group_admins")),
        sa.UniqueConstraint("group_id", "user_id", name=op.f("uq_group_admins_group_id")),
    )


def step_13_create_table_callback_tokens() -> None:
    op.create_table(
        "callback_tokens",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_callback_tokens_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_callback_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_callback_tokens_token_hash")),
    )


def step_14_create_table_draw_snapshots() -> None:
    op.create_table(
        "draw_snapshots",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("ledger_watermark", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("total_weight", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_draw_snapshots_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draw_snapshots")),
        sa.UniqueConstraint("campaign_id", name=op.f("uq_draw_snapshots_campaign_id")),
        sa.UniqueConstraint("snapshot_hash", name=op.f("uq_draw_snapshots_snapshot_hash")),
    )


def step_15_create_table_participants() -> None:
    op.create_table(
        "participants",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("private_reachable", sa.Boolean(), nullable=False),
        sa.Column(
            "qualification_state",
            sa.Enum(
                "pending",
                "eligible",
                "ineligible",
                "unknown",
                name="qualification_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("registered_revision", sa.Integer(), nullable=False),
        sa.Column("ticket_total_cache", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_participants_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_participants")),
        sa.UniqueConstraint("campaign_id", "user_id", name=op.f("uq_participants_campaign_id")),
    )


def step_16_create_table_prizes() -> None:
    op.create_table(
        "prizes",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("claim_instructions", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_prizes_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prizes")),
    )


def step_17_create_table_rule_revisions() -> None:
    op.create_table(
        "rule_revisions",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "rules",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("rules_hash", sa.String(length=64), nullable=False),
        sa.Column("frozen", sa.Boolean(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_rule_revisions_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_revisions")),
        sa.UniqueConstraint("campaign_id", "revision", name=op.f("uq_rule_revisions_campaign_id")),
    )


def step_18_create_table_activity_events() -> None:
    op.create_table(
        "activity_events",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_activity_events_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_activity_events_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_events")),
        sa.UniqueConstraint("chat_id", "message_id", name=op.f("uq_activity_events_chat_id")),
    )


def step_19_create_index_item() -> None:
    op.create_index(
        op.f("ix_activity_events_participant_id"),
        "activity_events",
        ["participant_id"],
        unique=False,
    )


UPGRADE_STEPS = (
    step_10_create_index_uq_campaigns_group_nonterminal,
    step_11_create_table_delivery_attempts,
    step_12_create_table_group_admins,
    step_13_create_table_callback_tokens,
    step_14_create_table_draw_snapshots,
    step_15_create_table_participants,
    step_16_create_table_prizes,
    step_17_create_table_rule_revisions,
    step_18_create_table_activity_events,
    step_19_create_index_item,
)
