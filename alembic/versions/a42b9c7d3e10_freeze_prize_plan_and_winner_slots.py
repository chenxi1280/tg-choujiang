"""freeze prize plan and track winner slots

Revision ID: a42b9c7d3e10
Revises: 0d7f5bb6c9a1
Create Date: 2026-08-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a42b9c7d3e10"
down_revision: str | Sequence[str] | None = "0d7f5bb6c9a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("prize_plan_hash", sa.String(length=64)))
    op.add_column("draw_snapshots", sa.Column("prize_plan_hash", sa.String(length=64)))
    op.alter_column("prizes", "quantity", new_column_name="winner_count")
    op.create_check_constraint("ck_prizes_position_positive", "prizes", "position > 0")
    op.create_check_constraint("ck_prizes_winner_count_positive", "prizes", "winner_count > 0")
    op.create_unique_constraint(
        "uq_prizes_campaign_position", "prizes", ["campaign_id", "position"]
    )
    op.add_column("draw_rounds", sa.Column("prize_slot_no", sa.Integer()))
    _backfill_prize_slots()
    op.alter_column("draw_rounds", "prize_slot_no", nullable=False)
    op.create_check_constraint(
        "ck_draw_rounds_prize_slot_positive", "draw_rounds", "prize_slot_no > 0"
    )
    op.create_unique_constraint(
        "uq_draw_rounds_prize_slot",
        "draw_rounds",
        ["draw_run_id", "prize_id", "prize_slot_no"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_draw_rounds_prize_slot", "draw_rounds", type_="unique")
    op.drop_constraint("ck_draw_rounds_prize_slot_positive", "draw_rounds", type_="check")
    op.drop_column("draw_rounds", "prize_slot_no")
    op.drop_constraint("uq_prizes_campaign_position", "prizes", type_="unique")
    op.drop_constraint("ck_prizes_winner_count_positive", "prizes", type_="check")
    op.drop_constraint("ck_prizes_position_positive", "prizes", type_="check")
    op.alter_column("prizes", "winner_count", new_column_name="quantity")
    op.drop_column("draw_snapshots", "prize_plan_hash")
    op.drop_column("campaigns", "prize_plan_hash")


def _backfill_prize_slots() -> None:
    op.execute(
        """
        UPDATE draw_rounds AS target
        SET prize_slot_no = ranked.slot_no
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY draw_run_id, prize_id
                       ORDER BY round_number, id
                   )::integer AS slot_no
            FROM draw_rounds
        ) AS ranked
        WHERE target.id = ranked.id
        """
    )
