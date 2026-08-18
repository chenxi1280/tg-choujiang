"""add group-scoped bonus facts

Revision ID: 8f2a4c9d1e70
Revises: 6b7d8e9f0a12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f2a4c9d1e70"
down_revision: str | Sequence[str] | None = "6b7d8e9f0a12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_columns()
    _backfill_group_identity()
    _replace_unique_constraints()


def _add_columns() -> None:
    op.add_column(
        "daily_progress",
        sa.Column("participation_group_id", sa.Uuid()),
    )
    op.create_foreign_key(
        "fk_daily_progress_participation_group",
        "daily_progress",
        "campaign_participation_groups",
        ["participation_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_daily_progress_participation_group_id",
        "daily_progress",
        ["participation_group_id"],
    )
    for table in ("invite_links", "invitations"):
        op.add_column(table, sa.Column("participation_group_id", sa.Uuid()))
        foreign_key_name = f"fk_{table}_participation_group"
        op.create_foreign_key(
            foreign_key_name,
            table,
            "campaign_participation_groups",
            ["participation_group_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table}_participation_group_id",
            table,
            ["participation_group_id"],
        )
    op.add_column("ticket_ledger", sa.Column("participation_group_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_ticket_ledger_participation_group",
        "ticket_ledger",
        "campaign_participation_groups",
        ["participation_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ticket_ledger_participation_group_id",
        "ticket_ledger",
        ["participation_group_id"],
    )


def _backfill_group_identity() -> None:
    op.execute(
        """
        UPDATE invite_links AS link
        SET participation_group_id = target.id
        FROM campaign_participation_groups AS target
        WHERE target.campaign_id = link.campaign_id
          AND target.is_primary = TRUE
          AND link.participation_group_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE invitations AS invitation
        SET participation_group_id = link.participation_group_id
        FROM invite_links AS link
        WHERE link.id = invitation.invite_link_id
          AND invitation.participation_group_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE ticket_ledger AS ledger
        SET participation_group_id = invitation.participation_group_id
        FROM invitations AS invitation
        WHERE ledger.source_ref = invitation.id::text
          AND ledger.participation_group_id IS NULL
        """
    )


def _replace_unique_constraints() -> None:
    op.drop_constraint("uq_daily_progress_campaign_id", "daily_progress", type_="unique")
    op.create_index(
        "uq_daily_progress_group_date",
        "daily_progress",
        ["campaign_id", "participant_id", "local_date", "participation_group_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_daily_progress_legacy_date",
        "daily_progress",
        ["campaign_id", "participant_id", "local_date"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NULL"),
    )
    op.drop_constraint("uq_invite_links_campaign_id", "invite_links", type_="unique")
    op.create_index(
        "uq_invite_links_group",
        "invite_links",
        ["campaign_id", "participant_id", "participation_group_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_invite_links_legacy",
        "invite_links",
        ["campaign_id", "participant_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NULL"),
    )
    op.drop_constraint("uq_invitations_campaign_id", "invitations", type_="unique")
    op.create_index(
        "uq_invitations_group_invitee",
        "invitations",
        ["campaign_id", "participation_group_id", "invitee_user_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NOT NULL"),
    )
    op.create_index(
        "uq_invitations_legacy_invitee",
        "invitations",
        ["campaign_id", "invitee_user_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NULL"),
    )


def downgrade() -> None:
    _guard_lossless_downgrade()
    op.drop_index("uq_invitations_legacy_invitee", table_name="invitations")
    op.drop_index("uq_invitations_group_invitee", table_name="invitations")
    op.create_unique_constraint(
        "uq_invitations_campaign_id",
        "invitations",
        ["campaign_id", "invitee_user_id"],
    )
    op.drop_index("uq_invite_links_legacy", table_name="invite_links")
    op.drop_index("uq_invite_links_group", table_name="invite_links")
    op.create_unique_constraint(
        "uq_invite_links_campaign_id",
        "invite_links",
        ["campaign_id", "participant_id"],
    )
    for table in ("invitations", "invite_links"):
        op.drop_index(f"ix_{table}_participation_group_id", table_name=table)
        op.drop_constraint(
            f"fk_{table}_participation_group",
            table,
            type_="foreignkey",
        )
        op.drop_column(table, "participation_group_id")
    op.drop_index("ix_ticket_ledger_participation_group_id", table_name="ticket_ledger")
    op.drop_constraint(
        "fk_ticket_ledger_participation_group",
        "ticket_ledger",
        type_="foreignkey",
    )
    op.drop_column("ticket_ledger", "participation_group_id")
    op.drop_index("uq_daily_progress_legacy_date", table_name="daily_progress")
    op.drop_index("uq_daily_progress_group_date", table_name="daily_progress")
    op.create_unique_constraint(
        "uq_daily_progress_campaign_id",
        "daily_progress",
        ["campaign_id", "participant_id", "local_date"],
    )
    op.drop_index("ix_daily_progress_participation_group_id", table_name="daily_progress")
    op.drop_constraint(
        "fk_daily_progress_participation_group",
        "daily_progress",
        type_="foreignkey",
    )
    op.drop_column("daily_progress", "participation_group_id")


def _guard_lossless_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM daily_progress
                WHERE participation_group_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM invite_links
                WHERE participation_group_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM invitations
                WHERE participation_group_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM ticket_ledger
                WHERE participation_group_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade: group-scoped bonus facts exist';
            END IF;
        END
        $$;
        """
    )
