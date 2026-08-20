"""add v2.4 draw commitments

Revision ID: e5c1a7d9b402
Revises: b4e7c3d91a20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5c1a7d9b402"
down_revision: str | Sequence[str] | None = "b4e7c3d91a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_operational_blocks",
        sa.Column("source", sa.String(length=32), server_default="telegram_outbox", nullable=False),
    )
    op.add_column(
        "campaign_operational_blocks",
        sa.Column("failure_count", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "campaign_operational_blocks",
        sa.Column("retry_exhausted_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        sa.text(
            """
            UPDATE campaign_operational_blocks AS block
               SET source = 'qualification_review'
             WHERE block.details IS NULL
               AND EXISTS (
                SELECT 1
                  FROM qualification_target_observations AS observation
                  JOIN participants AS participant
                    ON participant.id = observation.participant_id
                 WHERE participant.campaign_id = block.campaign_id
                   AND observation.target_kind = block.target_kind
                   AND observation.chat_id = block.chat_id
                   AND observation.state = 'unknown'
             )
            """
        )
    )
    op.create_table(
        "draw_commitments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=40), nullable=False),
        sa.Column("seed", sa.String(length=128), nullable=False),
        sa.Column("commitment_hash", sa.String(length=64), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revealed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id"),
        sa.UniqueConstraint("commitment_hash"),
    )


def downgrade() -> None:
    active = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM campaigns WHERE contract_version = 'v2.4' LIMIT 1"))
        .scalar()
    )
    if active:
        raise RuntimeError("cannot downgrade while v2.4 campaigns exist")
    op.drop_table("draw_commitments")
    op.drop_column("campaign_operational_blocks", "retry_exhausted_at")
    op.drop_column("campaign_operational_blocks", "failure_count")
    op.drop_column("campaign_operational_blocks", "source")
