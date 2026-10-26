"""Persist Inbox flood-control deadlines and the most recent failure evidence."""

import sqlalchemy as sa
from alembic import op

revision = "c33a1026ir01"
down_revision = "c33a1024bc03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("telegram_updates", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.add_column("telegram_updates", sa.Column("last_failure", sa.Text()))
    op.execute("UPDATE telegram_updates SET last_failure = last_error WHERE last_error IS NOT NULL")


def downgrade():
    waiting = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM telegram_updates "
            "WHERE state <> 'processed' AND next_attempt_at > now())"
        )
    )
    if waiting:
        raise RuntimeError("Inbox rate-limit deadline is pending; downgrade refused")
    op.drop_column("telegram_updates", "last_failure")
    op.drop_column("telegram_updates", "next_attempt_at")
