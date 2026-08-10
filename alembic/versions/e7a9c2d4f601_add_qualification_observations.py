"""add qualification observations

Revision ID: e7a9c2d4f601
Revises: d4c8a1f2b903
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7a9c2d4f601"
down_revision: str | Sequence[str] | None = "d4c8a1f2b903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qualification_checks",
        sa.Column(
            "observations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("qualification_checks", "observations", server_default=None)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM qualification_checks
                WHERE observations <> '{}'::jsonb
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: qualification observation audit facts exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_column("qualification_checks", "observations")
