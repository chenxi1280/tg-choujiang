"""add per-group activity progress

Revision ID: c6d4e8f1a205
Revises: b91e4d7a6c20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6d4e8f1a205"
down_revision: str | Sequence[str] | None = "b91e4d7a6c20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _replace_event_constraints_for_campaign_scope()
    op.create_index(
        "ix_campaigns_rules_gin",
        "campaigns",
        ["rules"],
        unique=False,
        postgresql_using="gin",
    )
    op.add_column(
        "daily_progress",
        sa.Column(
            "valid_message_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    _backfill_main_group_counts()
    op.alter_column("daily_progress", "valid_message_counts", server_default=None)


def downgrade() -> None:
    _guard_lossless_downgrade()
    op.drop_column("daily_progress", "valid_message_counts")
    op.drop_index("ix_campaigns_rules_gin", table_name="campaigns", postgresql_using="gin")
    op.drop_constraint(
        op.f("uq_invitee_activity_events_invitation_id"),
        "invitee_activity_events",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_invitee_activity_events_chat_id"),
        "invitee_activity_events",
        ["chat_id", "message_id"],
    )
    op.drop_constraint(op.f("uq_activity_events_campaign_id"), "activity_events", type_="unique")
    op.create_unique_constraint(
        op.f("uq_activity_events_chat_id"),
        "activity_events",
        ["chat_id", "message_id"],
    )


def _replace_event_constraints_for_campaign_scope() -> None:
    op.drop_constraint(op.f("uq_activity_events_chat_id"), "activity_events", type_="unique")
    op.create_unique_constraint(
        op.f("uq_activity_events_campaign_id"),
        "activity_events",
        ["campaign_id", "chat_id", "message_id"],
    )
    op.drop_constraint(
        op.f("uq_invitee_activity_events_chat_id"),
        "invitee_activity_events",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_invitee_activity_events_invitation_id"),
        "invitee_activity_events",
        ["invitation_id", "chat_id", "message_id"],
    )


def _backfill_main_group_counts() -> None:
    op.execute(
        """
        UPDATE daily_progress AS progress
        SET valid_message_counts = jsonb_build_object(
            groups.chat_id::text,
            progress.valid_message_count
        )
        FROM campaigns
        JOIN groups ON groups.id = campaigns.group_id
        WHERE progress.campaign_id = campaigns.id
          AND progress.valid_message_count > 0
        """
    )


def _guard_lossless_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM daily_progress
                WHERE (
                    SELECT count(*) FROM jsonb_object_keys(valid_message_counts)
                ) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: per-group activity progress exists';
            END IF;
            IF EXISTS (
                SELECT 1 FROM activity_events
                GROUP BY chat_id, message_id HAVING count(*) > 1
            ) OR EXISTS (
                SELECT 1 FROM invitee_activity_events
                GROUP BY chat_id, message_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: shared campaign message events exist';
            END IF;
        END
        $$;
        """
    )
