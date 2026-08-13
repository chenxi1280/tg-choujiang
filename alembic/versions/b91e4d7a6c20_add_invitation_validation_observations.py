"""add invitation validation observations

Revision ID: b91e4d7a6c20
Revises: f8b0d3e5a712
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b91e4d7a6c20"
down_revision: str | Sequence[str] | None = "f8b0d3e5a712"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invitations",
        sa.Column(
            "validation_observations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.alter_column("invitations", "validation_observations", server_default=None)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM invitations
                WHERE validation_observations <> '{}'::jsonb
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: invitation validation observations exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_column("invitations", "validation_observations")
