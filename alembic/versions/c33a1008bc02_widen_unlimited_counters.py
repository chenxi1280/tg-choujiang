"""Widen accumulated counts and snapshot weights for unlimited rewards."""

import sqlalchemy as sa
from alembic import op

revision = "c33a1008bc02"
down_revision = "c33a1008bf01"
branch_labels = None
depends_on = None

COUNTERS = (
    ("participants", "ticket_total_cache"),
    ("daily_progress", "valid_message_count"),
    ("daily_progress", "tickets_awarded"),
    ("user_group_message_totals", "total_count"),
    ("draw_entries", "weight"),
)


def upgrade():
    for table, column in COUNTERS:
        op.alter_column(table, column, existing_type=sa.Integer(), type_=sa.BigInteger())


def downgrade():
    for table, column in COUNTERS:
        count = op.get_bind().scalar(
            sa.text(
                f'SELECT count(*) FROM "{table}" '
                f'WHERE "{column}" NOT BETWEEN -2147483648 AND 2147483647'
            )
        )
        if count:
            raise RuntimeError(f"{table}.{column} exceeds 32-bit range; downgrade refused")
    for table, column in COUNTERS:
        op.alter_column(table, column, existing_type=sa.BigInteger(), type_=sa.Integer())
