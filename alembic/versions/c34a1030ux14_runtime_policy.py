"""Persist the UX retention activation boundary without changing lottery facts."""

import sqlalchemy as sa
from alembic import op

revision = "c34a1030ux14"
down_revision = "c33a1026ir01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_policies",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_runtime_policies"),
    )


def downgrade():
    op.drop_table("runtime_policies")
