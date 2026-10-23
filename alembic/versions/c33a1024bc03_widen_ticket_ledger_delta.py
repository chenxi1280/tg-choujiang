"""Keep individual reward entries as wide as accumulated ticket counts."""

import sqlalchemy as sa
from alembic import op

revision = "c33a1024bc03"
down_revision = "c33a1008bc02"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("ticket_ledger", "delta", existing_type=sa.Integer(), type_=sa.BigInteger())


def downgrade():
    oversized = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ticket_ledger WHERE delta NOT BETWEEN "
            "-2147483648 AND 2147483647)"
        )
    )
    if oversized:
        raise RuntimeError("ticket_ledger.delta exceeds 32-bit range; downgrade refused")
    op.alter_column("ticket_ledger", "delta", existing_type=sa.BigInteger(), type_=sa.Integer())
