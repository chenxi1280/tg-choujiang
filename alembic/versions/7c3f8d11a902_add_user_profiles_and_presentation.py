"""add user profiles and campaign presentation

Revision ID: 7c3f8d11a902
Revises: a42b9c7d3e10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7c3f8d11a902"
down_revision: str | Sequence[str] | None = "a42b9c7d3e10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("region", sa.String(length=32), nullable=True),
        sa.Column("region_editable_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("region_locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_draft",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("last_draft_saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_profiles")),
    )
    op.add_column(
        "campaigns",
        sa.Column(
            "presentation",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "presentation")
    op.drop_table("user_profiles")
