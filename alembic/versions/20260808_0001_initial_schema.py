"""initial schema

Revision ID: 567e7c3b621a
Revises:
Create Date: 2026-08-08
"""

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

from tg_lottery.migrations.schema_v1_core import UPGRADE_STEPS as CORE_STEPS
from tg_lottery.migrations.schema_v1_draw import UPGRADE_STEPS as DRAW_STEPS
from tg_lottery.migrations.schema_v1_runtime import UPGRADE_STEPS as RUNTIME_STEPS

revision: str = "567e7c3b621a"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    steps: tuple[Callable[[], None], ...] = RUNTIME_STEPS + CORE_STEPS + DRAW_STEPS
    for step in steps:
        step()


def downgrade() -> None:
    op.drop_table("winners")
    op.drop_index(
        op.f("ix_invitee_activity_events_invitation_id"), table_name="invitee_activity_events"
    )
    op.drop_table("invitee_activity_events")
    op.drop_table("invitations")
    op.drop_table("draw_rounds")
    op.drop_table("draw_entries")
    op.drop_table("ticket_ledger")
    op.drop_index(op.f("ix_qualification_checks_participant_id"), table_name="qualification_checks")
    op.drop_table("qualification_checks")
    op.drop_table("invite_links")
    op.drop_table("draw_runs")
    op.drop_table("daily_progress")
    op.drop_index(op.f("ix_activity_events_participant_id"), table_name="activity_events")
    op.drop_table("activity_events")
    op.drop_table("rule_revisions")
    op.drop_table("prizes")
    op.drop_table("participants")
    op.drop_table("draw_snapshots")
    op.drop_table("callback_tokens")
    op.drop_table("group_admins")
    op.drop_table("delivery_attempts")
    op.drop_index(
        "uq_campaigns_group_nonterminal",
        table_name="campaigns",
        postgresql_where=sa.text("state NOT IN ('drawn', 'archived', 'cancelled')"),
    )
    op.drop_table("campaigns")
    op.drop_table("worker_heartbeats")
    op.drop_table("telegram_updates")
    op.drop_table("telegram_outbox")
    op.drop_table("scheduled_jobs")
    op.drop_table("polling_offsets")
    op.drop_table("groups")
    op.drop_table("bot_runtime_leases")
    op.drop_table("audit_logs")
    op.drop_table("admin_sessions")
