from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select

from tests.test_multi_group_campaign import (
    ACTOR_ID,
    PRIMARY_CHAT_ID,
    REQUIREMENT_CHAT_ID,
    SECONDARY_CHAT_ID,
    _multi_group_draft,
    _prizes,
    _seed_participation_groups,
)
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import (
    CampaignParticipationGroup,
    CampaignRequirement,
    GroupAdmin,
    TelegramOutbox,
)
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.outbox import OutboxWorker

TEST_ENCRYPTION_KEY = urlsafe_b64encode(bytes(range(32))).decode()


async def _published_private_campaign(session_factory) -> object:
    groups = await _seed_participation_groups(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            GroupAdmin(
                group_id=groups[1].id,
                user_id=ACTOR_ID,
                source="administrator",
                active=True,
            )
        )
    service = CampaignService(partial(UnitOfWork, session_factory))
    campaign = await service.create_draft(
        _multi_group_draft(datetime.now(UTC), private=True), _prizes(), ACTOR_ID
    )
    await service.publish(campaign.id, ACTOR_ID, "lottery_test_bot")
    return campaign


async def _confirm_invites_and_project(session_factory, campaign) -> None:
    """模拟入口链接投递成功并执行投影接力 (回归 autoflush=False 缺陷)。"""
    cipher = SecretCipher(TEST_ENCRYPTION_KEY)
    worker = OutboxWorker(
        owner_id="test-owner",
        session_factory=session_factory,
        gateway=None,
        cipher=cipher,
    )
    invite_row_ids = await _mark_invites_confirmed(session_factory, campaign, cipher)
    for row_id in invite_row_ids:
        await worker._project_confirmation(row_id)


async def _mark_invites_confirmed(session_factory, campaign, cipher) -> list:
    invite_row_ids = []
    async with session_factory() as session, session.begin():
        rows = (
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.aggregate_id == str(campaign.id))
            )
        ).all()
        assert {row.operation for row in rows} == {"create_required_chat_invite"}
        for row in rows:
            row.state = OutboxState.CONFIRMED
            row.encrypted_remote_secret = cipher.encrypt(
                f"https://t.me/+private_{row.recipient_chat_id}",
                context=f"outbox-result:{row.id}",
            )
            invite_row_ids.append(row.id)
    return invite_row_ids


async def _operations(session_factory, campaign_id) -> set:
    async with session_factory() as session:
        return {
            (row.operation, row.recipient_chat_id)
            for row in (
                await session.execute(
                    select(TelegramOutbox.operation, TelegramOutbox.recipient_chat_id).where(
                        TelegramOutbox.aggregate_id == str(campaign_id)
                    )
                )
            ).all()
        }


async def test_private_invite_confirmation_projects_cards_and_links(
    session_factory,
) -> None:
    campaign = await _published_private_campaign(session_factory)

    await _confirm_invites_and_project(session_factory, campaign)

    assert await _operations(session_factory, campaign.id) == {
        ("send_campaign_card", PRIMARY_CHAT_ID),
        ("send_campaign_card", SECONDARY_CHAT_ID),
        ("create_required_chat_invite", SECONDARY_CHAT_ID),
        ("create_required_chat_invite", REQUIREMENT_CHAT_ID),
    }


async def test_private_targets_keep_encrypted_links_after_projection(
    session_factory,
) -> None:
    campaign = await _published_private_campaign(session_factory)

    await _confirm_invites_and_project(session_factory, campaign)

    async with session_factory() as session:
        secondary = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.chat_id_snapshot == SECONDARY_CHAT_ID
            )
        )
        requirement = await session.scalar(
            select(CampaignRequirement).where(
                CampaignRequirement.chat_id_snapshot == REQUIREMENT_CHAT_ID
            )
        )
    assert secondary is not None and secondary.encrypted_join_url is not None
    assert requirement is not None and requirement.encrypted_join_url is not None
