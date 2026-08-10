"""add campaign public reference

Revision ID: d4c8a1f2b903
Revises: 2a4e91b6f703
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4c8a1f2b903"
down_revision: str | Sequence[str] | None = "2a4e91b6f703"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PUBLIC_REF_DEFAULT = sa.text(
    "rtrim(translate(encode(uuid_send(gen_random_uuid()), 'base64'), '+/', '-_'), '=')"
)


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "public_ref",
            sa.String(length=22),
            server_default=_PUBLIC_REF_DEFAULT,
            nullable=True,
        ),
    )
    op.execute("UPDATE campaigns SET public_ref = DEFAULT WHERE public_ref IS NULL")
    op.create_unique_constraint("uq_campaigns_public_ref", "campaigns", ["public_ref"])
    op.alter_column(
        "campaigns",
        "public_ref",
        existing_type=sa.String(length=22),
        nullable=False,
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM campaigns) THEN "
        "RAISE EXCEPTION 'cannot downgrade while campaign public references exist'; "
        "END IF; END $$"
    )
    op.drop_constraint("uq_campaigns_public_ref", "campaigns", type_="unique")
    op.drop_column("campaigns", "public_ref")
