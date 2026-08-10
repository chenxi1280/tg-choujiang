"""add campaign draft publication source

Revision ID: f8b0d3e5a712
Revises: e7a9c2d4f601
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8b0d3e5a712"
down_revision: str | Sequence[str] | None = "e7a9c2d4f601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("source_draft_id", sa.String(64)))
    op.add_column("campaigns", sa.Column("source_draft_revision", sa.Integer()))
    op.create_unique_constraint("uq_campaigns_source_draft_id", "campaigns", ["source_draft_id"])


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM campaigns WHERE source_draft_id IS NOT NULL) THEN
                RAISE EXCEPTION
                    'cannot downgrade: campaign draft publication sources exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_constraint("uq_campaigns_source_draft_id", "campaigns", type_="unique")
    op.drop_column("campaigns", "source_draft_revision")
    op.drop_column("campaigns", "source_draft_id")
