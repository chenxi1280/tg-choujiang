import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


def _outbox_state() -> sa.Enum:
    return sa.Enum(
        "pending",
        "claimed",
        "retry_scheduled",
        "confirmed",
        "pre_accept_failed",
        "unknown_hold",
        "cancelled",
        name="outbox_state",
        native_enum=False,
        create_constraint=True,
    )


def _campaign_state() -> sa.Enum:
    return sa.Enum(
        "draft",
        "publishing",
        "scheduled",
        "running",
        "paused",
        "settling",
        "ready_to_draw",
        "drawing",
        "drawn",
        "archived",
        "cancelled",
        name="campaign_state",
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


def step_00_create_table_admin_sessions() -> None:
    op.create_table(
        "admin_sessions",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column(
            "data",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_admin_sessions")),
    )


def step_01_create_table_audit_logs() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )


def step_02_create_table_bot_runtime_leases() -> None:
    op.create_table(
        "bot_runtime_leases",
        sa.Column("bot_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("fence", sa.BigInteger(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", name=op.f("pk_bot_runtime_leases")),
    )


def step_03_create_table_groups() -> None:
    op.create_table(
        "groups",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("migrated_from_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "permissions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("permission_checked_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_groups")),
        sa.UniqueConstraint("chat_id", name=op.f("uq_groups_chat_id")),
        sa.UniqueConstraint("migrated_from_chat_id", name=op.f("uq_groups_migrated_from_chat_id")),
    )


def step_04_create_table_polling_offsets() -> None:
    op.create_table(
        "polling_offsets",
        sa.Column("bot_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("last_durable_update_id", sa.BigInteger(), nullable=True),
        sa.Column("last_poll_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("bot_id", name=op.f("pk_polling_offsets")),
    )


def step_05_create_table_scheduled_jobs() -> None:
    op.create_table(
        "scheduled_jobs",
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("business_key", sa.String(length=180), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "pending",
                "claimed",
                "retry_scheduled",
                "succeeded",
                "blocked",
                "failed_terminal",
                name="job_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_jobs")),
        sa.UniqueConstraint("business_key", name=op.f("uq_scheduled_jobs_business_key")),
    )


def step_06_create_table_telegram_outbox() -> None:
    op.create_table(
        "telegram_outbox",
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=40), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("recipient_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("business_key", sa.String(length=220), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "state",
            _outbox_state(),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gateway_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_outbox")),
        sa.UniqueConstraint("business_key", name=op.f("uq_telegram_outbox_business_key")),
    )


def step_07_create_table_telegram_updates() -> None:
    op.create_table(
        "telegram_updates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("bot_id", sa.BigInteger(), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("update_type", sa.String(length=40), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "received",
                "processing",
                "processed",
                "failed",
                name="update_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_updates")),
        sa.UniqueConstraint("bot_id", "update_id", name=op.f("uq_telegram_updates_bot_id")),
    )


def step_08_create_table_worker_heartbeats() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_name", sa.String(length=80), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("worker_name", name=op.f("pk_worker_heartbeats")),
    )


def step_09_create_table_campaigns() -> None:
    op.create_table(
        "campaigns",
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "state",
            _campaign_state(),
            nullable=False,
        ),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("preheat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draw_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column(
            "rules",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("published_message_id", sa.BigInteger(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name=op.f("fk_campaigns_group_id_groups"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )


UPGRADE_STEPS = (
    step_00_create_table_admin_sessions,
    step_01_create_table_audit_logs,
    step_02_create_table_bot_runtime_leases,
    step_03_create_table_groups,
    step_04_create_table_polling_offsets,
    step_05_create_table_scheduled_jobs,
    step_06_create_table_telegram_outbox,
    step_07_create_table_telegram_updates,
    step_08_create_table_worker_heartbeats,
    step_09_create_table_campaigns,
)
