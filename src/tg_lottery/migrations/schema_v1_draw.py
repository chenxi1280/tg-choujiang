import sqlalchemy as sa
from alembic import op


def _invitation_state() -> sa.Enum:
    return sa.Enum(
        "attribution_candidate",
        "joined_pending",
        "checking",
        "valid",
        "left_before_valid",
        "invalid",
        "validation_unknown",
        "expired_unqualified",
        name="invitation_state",
        native_enum=False,
        create_constraint=True,
    )


def _prize_claim_state() -> sa.Enum:
    return sa.Enum(
        "pending",
        "claimed",
        "forfeited",
        name="prize_claim_state",
        native_enum=False,
        create_constraint=True,
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def step_20_create_table_daily_progress() -> None:
    op.create_table(
        "daily_progress",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("valid_message_count", sa.Integer(), nullable=False),
        sa.Column("tickets_awarded", sa.Integer(), nullable=False),
        sa.Column("last_content_hash", sa.String(length=64), nullable=True),
        sa.Column("last_content_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_daily_progress_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_daily_progress_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_progress")),
        sa.UniqueConstraint(
            "campaign_id",
            "participant_id",
            "local_date",
            name=op.f("uq_daily_progress_campaign_id"),
        ),
    )


def step_21_create_table_draw_runs() -> None:
    op.create_table(
        "draw_runs",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=40), nullable=False),
        sa.Column("random_input", sa.String(length=128), nullable=False),
        sa.Column("started_by", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            ["snapshot_id"],
            ["draw_snapshots.id"],
            name=op.f("fk_draw_runs_snapshot_id_draw_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draw_runs")),
        sa.UniqueConstraint(
            "snapshot_id", "algorithm_version", name=op.f("uq_draw_runs_snapshot_id")
        ),
    )


def step_22_create_table_invite_links() -> None:
    op.create_table(
        "invite_links",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("link_hash", sa.String(length=64), nullable=False),
        sa.Column("encrypted_url", sa.LargeBinary(), nullable=True),
        sa.Column("telegram_name", sa.String(length=32), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remote_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_invite_links_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_invite_links_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invite_links")),
        sa.UniqueConstraint(
            "campaign_id", "participant_id", name=op.f("uq_invite_links_campaign_id")
        ),
        sa.UniqueConstraint("link_hash", name=op.f("uq_invite_links_link_hash")),
    )


def step_23_create_table_qualification_checks() -> None:
    op.create_table(
        "qualification_checks",
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "eligible",
                "ineligible",
                "unknown",
                name="qualification_check_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("observed_status", sa.String(length=40), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_qualification_checks_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qualification_checks")),
    )


def step_24_create_index_item() -> None:
    op.create_index(
        op.f("ix_qualification_checks_participant_id"),
        "qualification_checks",
        ["participant_id"],
        unique=False,
    )


def step_25_create_table_ticket_ledger() -> None:
    op.create_table(
        "ticket_ledger",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "popularity",
                "invitation",
                "reversal",
                name="ticket_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("business_key", sa.String(length=200), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
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
            name=op.f("fk_ticket_ledger_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_ticket_ledger_participant_id_participants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_ledger")),
        sa.UniqueConstraint("business_key", name=op.f("uq_ticket_ledger_business_key")),
    )


def step_26_create_table_draw_entries() -> None:
    op.create_table(
        "draw_entries",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("qualification_observation_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_draw_entries_participant_id_participants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["qualification_observation_id"],
            ["qualification_checks.id"],
            name=op.f("fk_draw_entries_qualification_observation_id_qualification_checks"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["draw_snapshots.id"],
            name=op.f("fk_draw_entries_snapshot_id_draw_snapshots"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draw_entries")),
        sa.UniqueConstraint(
            "snapshot_id", "participant_id", name=op.f("uq_draw_entries_snapshot_id")
        ),
    )


def step_27_create_table_draw_rounds() -> None:
    op.create_table(
        "draw_rounds",
        sa.Column("draw_run_id", sa.Uuid(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("prize_id", sa.Uuid(), nullable=False),
        sa.Column("range_start", sa.BigInteger(), nullable=False),
        sa.Column("range_end", sa.BigInteger(), nullable=False),
        sa.Column("selected_value", sa.BigInteger(), nullable=True),
        sa.Column("selected_participant_id", sa.Uuid(), nullable=True),
        sa.Column("undistributed", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["draw_run_id"],
            ["draw_runs.id"],
            name=op.f("fk_draw_rounds_draw_run_id_draw_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prize_id"],
            ["prizes.id"],
            name=op.f("fk_draw_rounds_prize_id_prizes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["selected_participant_id"],
            ["participants.id"],
            name=op.f("fk_draw_rounds_selected_participant_id_participants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draw_rounds")),
        sa.UniqueConstraint("draw_run_id", "round_number", name=op.f("uq_draw_rounds_draw_run_id")),
    )


def step_28_create_table_invitations() -> None:
    op.create_table(
        "invitations",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("invite_link_id", sa.Uuid(), nullable=False),
        sa.Column("inviter_participant_id", sa.Uuid(), nullable=False),
        sa.Column("invitee_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            _invitation_state(),
            nullable=False,
        ),
        sa.Column("candidate_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_version", sa.Integer(), nullable=False),
        sa.Column("validation_error", sa.String(length=120), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_invitations_campaign_id_campaigns"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invite_link_id"],
            ["invite_links.id"],
            name=op.f("fk_invitations_invite_link_id_invite_links"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inviter_participant_id"],
            ["participants.id"],
            name=op.f("fk_invitations_inviter_participant_id_participants"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitations")),
        sa.UniqueConstraint(
            "campaign_id", "invitee_user_id", name=op.f("uq_invitations_campaign_id")
        ),
    )


def step_29_create_table_invitee_activity_events() -> None:
    op.create_table(
        "invitee_activity_events",
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitations.id"],
            name=op.f("fk_invitee_activity_events_invitation_id_invitations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invitee_activity_events")),
        sa.UniqueConstraint(
            "chat_id", "message_id", name=op.f("uq_invitee_activity_events_chat_id")
        ),
    )


def step_30_create_index_item() -> None:
    op.create_index(
        op.f("ix_invitee_activity_events_invitation_id"),
        "invitee_activity_events",
        ["invitation_id"],
        unique=False,
    )


def step_31_create_table_winners() -> None:
    op.create_table(
        "winners",
        sa.Column("draw_run_id", sa.Uuid(), nullable=False),
        sa.Column("draw_round_id", sa.Uuid(), nullable=False),
        sa.Column("prize_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "claim_state",
            _prize_claim_state(),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["draw_round_id"],
            ["draw_rounds.id"],
            name=op.f("fk_winners_draw_round_id_draw_rounds"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draw_run_id"],
            ["draw_runs.id"],
            name=op.f("fk_winners_draw_run_id_draw_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name=op.f("fk_winners_participant_id_participants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prize_id"],
            ["prizes.id"],
            name=op.f("fk_winners_prize_id_prizes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_winners")),
        sa.UniqueConstraint("draw_run_id", "draw_round_id", name=op.f("uq_winners_draw_run_id")),
    )


UPGRADE_STEPS = (
    step_20_create_table_daily_progress,
    step_21_create_table_draw_runs,
    step_22_create_table_invite_links,
    step_23_create_table_qualification_checks,
    step_24_create_index_item,
    step_25_create_table_ticket_ledger,
    step_26_create_table_draw_entries,
    step_27_create_table_draw_rounds,
    step_28_create_table_invitations,
    step_29_create_table_invitee_activity_events,
    step_30_create_index_item,
    step_31_create_table_winners,
)
