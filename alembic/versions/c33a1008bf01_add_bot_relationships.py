"""Durable startup and private bot relationship evidence."""

import sqlalchemy as sa
from alembic import op

revision = "c33a1008bf01"
down_revision = "c30a1004ad01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bot_relationships",
        sa.Column("bot_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("start_update_id", sa.BigInteger()),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("observed_update_id", sa.BigInteger()),
        sa.Column("blocked", sa.Boolean()),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
    )


def downgrade():
    count = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM campaigns "
            "WHERE rules->'platform_policy'->>'version' = 'official-v1'"
        )
    )
    if count:
        raise RuntimeError("official policy campaigns exist; incompatible downgrade refused")
    op.drop_table("bot_relationships")
