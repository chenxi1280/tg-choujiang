"""add region audit and registration ticket

Revision ID: 2a4e91b6f703
Revises: 7c3f8d11a902
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a4e91b6f703"
down_revision: str | Sequence[str] | None = "7c3f8d11a902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TICKET_SOURCE_CHECK = "ck_ticket_ledger_ticket_source"


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("region_selected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "user_region_changes",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("old_region", sa.String(length=32), nullable=True),
        sa.Column("new_region", sa.String(length=32), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_region_changes")),
    )
    op.create_index(
        op.f("ix_user_region_changes_user_id"),
        "user_region_changes",
        ["user_id"],
    )
    _drop_ticket_source_check()
    op.alter_column(
        "ticket_ledger",
        "source_type",
        existing_type=sa.String(length=10),
        type_=sa.String(length=12),
        existing_nullable=False,
    )
    _create_ticket_source_check(include_registration=True)


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM ticket_ledger WHERE source_type = 'registration') THEN "
        "RAISE EXCEPTION 'cannot downgrade while registration tickets exist'; "
        "END IF; END $$"
    )
    _drop_ticket_source_check()
    op.alter_column(
        "ticket_ledger",
        "source_type",
        existing_type=sa.String(length=12),
        type_=sa.String(length=10),
        existing_nullable=False,
    )
    _create_ticket_source_check(include_registration=False)
    op.drop_index(op.f("ix_user_region_changes_user_id"), table_name="user_region_changes")
    op.drop_table("user_region_changes")
    op.drop_column("user_profiles", "region_selected_at")


def _drop_ticket_source_check() -> None:
    constraint_name = op.f(_TICKET_SOURCE_CHECK)
    op.drop_constraint(constraint_name, "ticket_ledger", type_="check")


def _create_ticket_source_check(*, include_registration: bool) -> None:
    values = ["popularity", "invitation", "reversal"]
    if include_registration:
        values.insert(0, "registration")
    options = ", ".join(f"'{value}'" for value in values)
    op.create_check_constraint(
        op.f(_TICKET_SOURCE_CHECK),
        "ticket_ledger",
        f"source_type IN ({options})",
    )
