"""add explicit database grants for winner designation operators

Revision ID: 9a7d3c5e8b20
Revises: f2d6a8c1e740
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a7d3c5e8b20"
down_revision: str | Sequence[str] | None = "f2d6a8c1e740"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "winner_designation_operators",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("note", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("user_id > 0", name="user_id_positive"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("winner_designation_operators")
