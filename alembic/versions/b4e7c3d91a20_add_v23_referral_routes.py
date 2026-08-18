"""add v2.3 referral routes and group membership facts

Revision ID: b4e7c3d91a20
Revises: 8f2a4c9d1e70
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4e7c3d91a20"
down_revision: str | Sequence[str] | None = "8f2a4c9d1e70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _allow_parallel_campaigns()
    _create_referral_routes()
    _version_invite_links()
    _backfill_routes()
    _create_membership_facts()
    _extend_invitation_records()


def _allow_parallel_campaigns() -> None:
    op.drop_index(
        "uq_campaign_participation_active_group",
        table_name="campaign_participation_groups",
    )
    op.create_index(
        "ix_campaign_participation_active_group",
        "campaign_participation_groups",
        ["group_id"],
        postgresql_where=sa.text("released_at IS NULL"),
    )


def _create_referral_routes() -> None:
    op.create_table(
        "campaign_referral_routes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("public_ref", sa.String(length=22), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("participation_group_id", sa.Uuid(), nullable=False),
        sa.Column("bonus_join_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'join_only', 'cancelled', 'blocked')",
            name=op.f("ck_campaign_referral_routes_referral_route_status"),
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["participation_group_id"],
            ["campaign_participation_groups.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_ref", name="uq_campaign_referral_routes_public_ref"),
        sa.UniqueConstraint(
            "campaign_id",
            "participant_id",
            "participation_group_id",
            name="uq_campaign_referral_routes_scope",
        ),
    )
    op.create_index(
        "ix_campaign_referral_routes_participation_group_id",
        "campaign_referral_routes",
        ["participation_group_id"],
    )


def _version_invite_links() -> None:
    op.add_column("invite_links", sa.Column("referral_route_id", sa.Uuid()))
    op.add_column(
        "invite_links",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "invite_links",
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column("invite_links", sa.Column("revoked_reason", sa.String(length=80)))
    op.alter_column(
        "invite_links", "expire_at", existing_type=sa.DateTime(timezone=True), nullable=True
    )
    op.create_foreign_key(
        "fk_invite_links_referral_route_id_campaign_referral_routes",
        "invite_links",
        "campaign_referral_routes",
        ["referral_route_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_invite_links_referral_route_id",
        "invite_links",
        ["referral_route_id"],
    )
    op.drop_index("uq_invite_links_group", table_name="invite_links")


def _backfill_routes() -> None:
    op.execute(
        """
        INSERT INTO campaign_referral_routes (
            id,
            public_ref,
            campaign_id,
            participant_id,
            participation_group_id,
            bonus_join_deadline,
            status,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            rtrim(
                translate(encode(uuid_send(gen_random_uuid()), 'base64'), '+/', '-_'),
                '='
            ),
            link.campaign_id,
            link.participant_id,
            link.participation_group_id,
            campaign.cutoff_at - make_interval(
                hours => COALESCE(
                    (campaign.rules #>> '{invite,retention_hours}')::integer,
                    24
                )
            ),
            CASE WHEN now() > campaign.cutoff_at THEN 'join_only' ELSE 'active' END,
            link.created_at,
            link.updated_at
        FROM invite_links AS link
        JOIN campaigns AS campaign ON campaign.id = link.campaign_id
        WHERE link.participation_group_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE invite_links AS link
        SET referral_route_id = route.id
        FROM campaign_referral_routes AS route
        WHERE route.campaign_id = link.campaign_id
          AND route.participant_id = link.participant_id
          AND route.participation_group_id = link.participation_group_id
          AND link.participation_group_id IS NOT NULL
        """
    )
    op.create_index(
        "uq_invite_links_current_route",
        "invite_links",
        ["referral_route_id"],
        unique=True,
        postgresql_where=sa.text("referral_route_id IS NOT NULL AND is_current"),
    )
    op.create_index(
        "uq_invite_links_route_version",
        "invite_links",
        ["referral_route_id", "version"],
        unique=True,
        postgresql_where=sa.text("referral_route_id IS NOT NULL"),
    )
    op.create_index(
        "uq_invite_links_unrouted_group",
        "invite_links",
        ["campaign_id", "participant_id", "participation_group_id"],
        unique=True,
        postgresql_where=sa.text(
            "participation_group_id IS NOT NULL AND referral_route_id IS NULL"
        ),
    )


def _create_membership_facts() -> None:
    op.create_table(
        "group_membership_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("first_observed_member_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_source_invite_link_id", sa.Uuid()),
        sa.Column("last_observed_state", sa.String(length=16), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("certainty", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "last_observed_state IN ('member', 'left', 'unknown')",
            name=op.f("ck_group_membership_facts_group_membership_state"),
        ),
        sa.CheckConstraint(
            "certainty IN ('confirmed', 'unknown')",
            name=op.f("ck_group_membership_facts_group_membership_certainty"),
        ),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["first_source_invite_link_id"], ["invite_links.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_membership_facts_identity"),
    )


def _extend_invitation_records() -> None:
    op.add_column("invitations", sa.Column("final_reason", sa.String(length=80)))
    op.add_column(
        "invitations",
        sa.Column("invitee_display_name_snapshot", sa.String(length=255)),
    )


def downgrade() -> None:
    _guard_lossless_downgrade()
    op.drop_column("invitations", "invitee_display_name_snapshot")
    op.drop_column("invitations", "final_reason")
    op.drop_table("group_membership_facts")
    op.drop_index("uq_invite_links_unrouted_group", table_name="invite_links")
    op.drop_index("uq_invite_links_route_version", table_name="invite_links")
    op.drop_index("uq_invite_links_current_route", table_name="invite_links")
    op.execute("UPDATE invite_links SET referral_route_id = NULL")
    op.drop_index("ix_invite_links_referral_route_id", table_name="invite_links")
    op.drop_constraint(
        "fk_invite_links_referral_route_id_campaign_referral_routes",
        "invite_links",
        type_="foreignkey",
    )
    op.drop_column("invite_links", "referral_route_id")
    op.drop_column("invite_links", "revoked_reason")
    op.drop_column("invite_links", "is_current")
    op.drop_column("invite_links", "version")
    op.alter_column(
        "invite_links",
        "expire_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.create_index(
        "uq_invite_links_group",
        "invite_links",
        ["campaign_id", "participant_id", "participation_group_id"],
        unique=True,
        postgresql_where=sa.text("participation_group_id IS NOT NULL"),
    )
    op.drop_table("campaign_referral_routes")
    op.drop_index(
        "ix_campaign_participation_active_group",
        table_name="campaign_participation_groups",
    )
    op.create_index(
        "uq_campaign_participation_active_group",
        "campaign_participation_groups",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def _guard_lossless_downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM group_membership_facts)
               OR EXISTS (SELECT 1 FROM invite_links WHERE version > 1)
               OR EXISTS (SELECT 1 FROM invite_links WHERE expire_at IS NULL)
               OR EXISTS (SELECT 1 FROM campaigns WHERE contract_version = 'v2.3')
               OR EXISTS (
                    SELECT group_id
                    FROM campaign_participation_groups
                    WHERE released_at IS NULL
                    GROUP BY group_id
                    HAVING count(*) > 1
               )
            THEN
                RAISE EXCEPTION 'cannot downgrade: v2.3 invitation facts exist';
            END IF;
        END
        $$;
        """
    )
