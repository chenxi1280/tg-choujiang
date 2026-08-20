"""add winner designations

Revision ID: f2d6a8c1e740
Revises: e5c1a7d9b402
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2d6a8c1e740"
down_revision: str | Sequence[str] | None = "e5c1a7d9b402"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _cancel_pending_commitment_notices()
    _create_designation_table()
    _add_round_selection_columns()


def _cancel_pending_commitment_notices() -> None:
    op.execute(
        sa.text(
            """
            UPDATE telegram_outbox
               SET state = 'cancelled',
                   updated_at = now(),
                   last_error = COALESCE(last_error, 'group commitment notices disabled')
             WHERE operation = 'send_draw_commitment'
               AND state IN (
                   'pending', 'claimed', 'retry_scheduled',
                   'pre_accept_failed', 'unknown_hold'
               )
            """
        )
    )


def _create_designation_table() -> None:
    op.create_table(
        "winner_designations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("prize_id", sa.Uuid(), nullable=False),
        sa.Column("prize_slot_no", sa.Integer(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("designated_by", sa.BigInteger(), nullable=False),
        sa.Column("designated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("prize_slot_no > 0", name="prize_slot_positive"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prize_id"], ["prizes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id",
            "prize_id",
            "prize_slot_no",
            name="uq_winner_designations_prize_slot",
        ),
    )


def _add_round_selection_columns() -> None:
    op.add_column(
        "draw_rounds",
        sa.Column("selection_mode", sa.String(length=16), server_default="random", nullable=False),
    )
    op.add_column("draw_rounds", sa.Column("designation_id", sa.Uuid()))
    op.create_check_constraint(
        "ck_draw_rounds_selection_mode",
        "draw_rounds",
        "selection_mode IN ('random', 'designated')",
    )
    op.create_foreign_key(
        "fk_draw_rounds_designation_id_winner_designations",
        "draw_rounds",
        "winner_designations",
        ["designation_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_draw_rounds_designation_id_winner_designations", "draw_rounds", type_="foreignkey"
    )
    op.drop_constraint("ck_draw_rounds_selection_mode", "draw_rounds", type_="check")
    op.drop_column("draw_rounds", "designation_id")
    op.drop_column("draw_rounds", "selection_mode")
    op.drop_table("winner_designations")
