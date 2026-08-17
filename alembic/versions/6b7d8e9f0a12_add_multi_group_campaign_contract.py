"""add multi-group campaign contract

Revision ID: 6b7d8e9f0a12
Revises: 3f1d9a7c21b4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b7d8e9f0a12"
down_revision: str | Sequence[str] | None = "3f1d9a7c21b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_participation_groups()
    _create_requirements()
    _create_qualification_target_observations()
    _create_operational_blocks()
    _extend_campaign_and_qualification_models()
    _backfill_participation_groups()
    _backfill_requirements()
    op.drop_index("uq_campaigns_group_nonterminal", table_name="campaigns")


def _create_participation_groups() -> None:
    op.create_table(
        "campaign_participation_groups",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("chat_id_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("title_snapshot", sa.String(length=255), nullable=False),
        sa.Column("username_snapshot", sa.String(length=64)),
        sa.Column("private", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("public_join_url", sa.Text()),
        sa.Column("encrypted_join_url", sa.LargeBinary()),
        sa.Column("join_url_hash", sa.String(length=64)),
        sa.Column("invite_created_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "permission_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column(
            "publication_state", sa.String(length=24), server_default="pending", nullable=False
        ),
        sa.Column("published_message_id", sa.BigInteger()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "position > 0", name=op.f("ck_campaign_participation_groups_position_positive")
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "group_id", name="uq_campaign_participation_group"),
        sa.UniqueConstraint("campaign_id", "position", name="uq_campaign_participation_position"),
    )
    _create_participation_indexes()


def _create_participation_indexes() -> None:
    op.create_index(
        "uq_campaign_participation_primary",
        "campaign_participation_groups",
        ["campaign_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index(
        "uq_campaign_participation_active_group",
        "campaign_participation_groups",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def _create_requirements() -> None:
    op.create_table(
        "campaign_requirements",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("target_group_id", sa.Uuid()),
        sa.Column("chat_id_snapshot", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("chat_type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=64)),
        sa.Column("private", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("public_join_url", sa.Text()),
        sa.Column("encrypted_join_url", sa.LargeBinary()),
        sa.Column("join_url_hash", sa.String(length=64)),
        sa.Column("invite_created_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("message_threshold", sa.Integer()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("position > 0", name=op.f("ck_campaign_requirements_position_positive")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_group_id"], ["groups.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "chat_id_snapshot", name="uq_campaign_requirement_chat"),
        sa.UniqueConstraint("campaign_id", "position", name="uq_campaign_requirement_position"),
    )


def _create_qualification_target_observations() -> None:
    op.create_table(
        "qualification_target_observations",
        sa.Column("check_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("observed_status", sa.String(length=40)),
        sa.Column("error_code", sa.String(length=80)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["check_id"], ["qualification_checks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "check_id",
            "target_kind",
            "chat_id",
            "attempt_number",
            name="uq_qualification_target_attempt",
        ),
    )
    op.create_index(
        op.f("ix_qualification_target_observations_check_id"),
        "qualification_target_observations",
        ["check_id"],
    )
    op.create_index(
        op.f("ix_qualification_target_observations_participant_id"),
        "qualification_target_observations",
        ["participant_id"],
    )


def _create_operational_blocks() -> None:
    op.create_table(
        "campaign_operational_blocks",
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_campaign_operational_block_active",
        "campaign_operational_blocks",
        ["campaign_id", "target_kind", "chat_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def _extend_campaign_and_qualification_models() -> None:
    op.add_column(
        "campaigns",
        sa.Column("contract_version", sa.String(length=16), server_default="v1", nullable=False),
    )
    op.add_column(
        "qualification_checks",
        sa.Column(
            "participation_groups_state",
            _qualification_state_enum("qualification_participation_state"),
        ),
    )
    op.add_column(
        "qualification_checks",
        sa.Column(
            "requirements_state",
            _qualification_state_enum("qualification_requirements_state"),
        ),
    )
    op.add_column("participants", sa.Column("registered_via_participation_group_id", sa.Uuid()))
    op.create_foreign_key(
        op.f("fk_participants_registered_via_participation_group_id_campaign_participation_groups"),
        "participants",
        "campaign_participation_groups",
        ["registered_via_participation_group_id"],
        ["id"],
        ondelete="SET NULL",
    )


def _qualification_state_enum(name: str) -> sa.Enum:
    return sa.Enum(
        "pending",
        "eligible",
        "ineligible",
        "unknown",
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _backfill_participation_groups() -> None:
    op.execute(
        """
        INSERT INTO campaign_participation_groups (
            id, campaign_id, group_id, position, is_primary, chat_id_snapshot,
            title_snapshot, username_snapshot, private, public_join_url,
            encrypted_join_url, invite_created_at, frozen_at, publication_state,
            published_message_id, published_at, released_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.id, g.id, 1, true, g.chat_id, g.title,
               c.presentation->'main_chat'->>'username',
               COALESCE((c.presentation->'main_chat'->>'private')::boolean, true),
               c.presentation->'main_chat'->>'join_url',
               CASE
                   WHEN c.presentation->'main_chat'->>'join_url_encrypted' IS NULL THEN NULL
                   ELSE decode(
                       rpad(
                           translate(
                               c.presentation->'main_chat'->>'join_url_encrypted',
                               '-_', '+/'
                           ),
                           CEIL(
                               length(c.presentation->'main_chat'->>'join_url_encrypted') / 4.0
                           )::int * 4,
                           '='
                       ),
                       'base64'
                   )
               END,
               NULLIF(c.presentation->'main_chat'->>'invite_created_at', '')::timestamptz,
               c.updated_at,
               CASE WHEN c.published_message_id IS NULL THEN 'pending' ELSE 'confirmed' END,
               c.published_message_id,
               CASE WHEN c.published_message_id IS NULL THEN NULL ELSE c.updated_at END,
               CASE WHEN c.state IN ('drawn','archived','cancelled')
                    THEN c.updated_at ELSE NULL END,
               c.created_at, c.updated_at
        FROM campaigns c JOIN groups g ON g.id = c.group_id
        """
    )


def _backfill_requirements() -> None:
    op.execute(
        """
        INSERT INTO campaign_requirements (
            id, campaign_id, target_group_id, chat_id_snapshot, position,
            chat_type, title, username, private, public_join_url,
            encrypted_join_url, invite_created_at, message_threshold,
            resolved_at, frozen_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.id,
               CASE WHEN COALESCE(item->>'type', 'group') = 'channel' THEN NULL ELSE g.id END,
               (item->>'id')::bigint, ordinal::int,
               COALESCE(item->>'type', 'group'), COALESCE(item->>'title', '未命名群组/频道'),
               item->>'username', COALESCE((item->>'private')::boolean, true), item->>'join_url',
               CASE WHEN item->>'join_url_encrypted' IS NULL THEN NULL
                    ELSE decode(
                        rpad(
                            translate(item->>'join_url_encrypted', '-_', '+/'),
                            CEIL(length(item->>'join_url_encrypted') / 4.0)::int * 4,
                            '='
                        ),
                        'base64'
                    ) END,
               NULLIF(item->>'invite_created_at', '')::timestamptz,
               NULLIF(c.rules->'message_thresholds'->>(item->>'id'), '')::int,
               c.updated_at, c.updated_at, c.created_at, c.updated_at
        FROM campaigns c
        CROSS JOIN LATERAL jsonb_array_elements(
            COALESCE(c.presentation->'required_chats', '[]'::jsonb)
        )
             WITH ORDINALITY AS required(item, ordinal)
        LEFT JOIN groups g ON g.chat_id = (item->>'id')::bigint
        WHERE (item->>'id')::bigint <> (SELECT chat_id FROM groups WHERE id = c.group_id)
        """
    )
    _backfill_legacy_required_channel()


def _backfill_legacy_required_channel() -> None:
    op.execute(
        """
        INSERT INTO campaign_requirements (
            id, campaign_id, target_group_id, chat_id_snapshot, position,
            chat_type, title, private, resolved_at, frozen_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), c.id, NULL,
               (c.rules->>'required_channel_id')::bigint,
               COALESCE((SELECT max(r.position) FROM campaign_requirements r
                         WHERE r.campaign_id = c.id), 0) + 1,
               'channel', '历史准入频道', true,
               c.updated_at, c.updated_at, c.created_at, c.updated_at
        FROM campaigns c
        WHERE c.rules->>'required_channel_id' IS NOT NULL
          AND (c.rules->>'required_channel_id')::bigint <>
              (SELECT chat_id FROM groups WHERE id = c.group_id)
          AND NOT EXISTS (
              SELECT 1 FROM campaign_requirements r
              WHERE r.campaign_id = c.id
                AND r.chat_id_snapshot = (c.rules->>'required_channel_id')::bigint
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (
                SELECT campaign_id FROM campaign_participation_groups
                GROUP BY campaign_id HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade: multi-group campaign data exists';
            END IF;
        END $$;
        """
    )
    op.create_index(
        "uq_campaigns_group_nonterminal",
        "campaigns",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('drawn', 'archived', 'cancelled')"),
    )
    op.drop_constraint(
        op.f("fk_participants_registered_via_participation_group_id_campaign_participation_groups"),
        "participants",
        type_="foreignkey",
    )
    op.drop_column("participants", "registered_via_participation_group_id")
    op.drop_constraint("qualification_requirements_state", "qualification_checks", type_="check")
    op.drop_constraint("qualification_participation_state", "qualification_checks", type_="check")
    op.drop_column("qualification_checks", "requirements_state")
    op.drop_column("qualification_checks", "participation_groups_state")
    op.drop_column("campaigns", "contract_version")
    op.drop_table("campaign_operational_blocks")
    op.drop_table("qualification_target_observations")
    op.drop_table("campaign_requirements")
    op.drop_table("campaign_participation_groups")
