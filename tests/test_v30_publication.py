import pytest
from sqlalchemy import func, select

from tests.v30_helpers import FIRST, START, rules_for, seed_v3
from tg_lottery.application.publication import add_campaign_card_outbox, campaign_card_payload
from tg_lottery.application.rule_publication import project_rule_page, queue_rule_states
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, Prize, TelegramOutbox
from tg_lottery.presentation.participation_rules import rule_manifest, utf16_units


@pytest.mark.parametrize("cover", [None, "file-id"])
def test_full_rules_pages_keep_each_condition_and_same_activity_link(cover):
    rules = rules_for("message")
    chats = [
        {
            "id": -100900 - i,
            "title": f"第{i}个关注目标" + "完整名称" * 14,
            "type": "channel",
            "join_url": f"https://t.me/channel_{i}",
        }
        for i in range(50)
    ]
    payload = dict(
        name="多条件抽奖",
        campaign_ref="abcdefgh",
        bot_username="lottery_test_bot",
        rules=rules.model_dump(mode="json"),
        timezone="Asia/Shanghai",
        cutoff_at=START.isoformat(),
        draw_at=START.isoformat(),
        prizes=[],
        presentation=dict(required_chats=chats, cover_file_id=cover),
    )
    card, pages = rule_manifest(payload)
    assert pages and len(pages) >= 2
    assert all(utf16_units(page) <= 3500 for page in pages)
    assert all(f"https://t.me/channel_{i}" in "".join(pages) for i in range(50))
    assert "join_abcdefgh" in card and "join_abcdefgh" in "".join(pages)
    assert "每天 2 条" in "".join(pages)


async def long_campaign(factory):
    cid, _pid = await seed_v3(factory, rules_for("message"))
    async with factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.state = CampaignState.PUBLISHING
        campaign.presentation = campaign.presentation | {
            "cover_file_id": "cover",
            "required_chats": [
                {
                    "id": -100900 - i,
                    "title": f"关注{i}" + "完整名称" * 20,
                    "type": "channel",
                    "join_url": f"https://t.me/target_{i}",
                }
                for i in range(25)
            ],
        }
        prizes = list(await session.scalars(select(Prize).where(Prize.campaign_id == cid)))
        add_campaign_card_outbox(
            session,
            campaign=campaign,
            recipient_chat_id=FIRST,
            bot_username="lottery_test_bot",
            prizes=prizes,
        )
    return cid


async def test_failed_or_unknown_page_does_not_publish_card_and_cancel_syncs_confirmed_pages(
    session_factory,
):
    cid = await long_campaign(session_factory)
    async with session_factory() as session, session.begin():
        first = await session.scalar(select(TelegramOutbox))
        assert first.operation == "send_campaign_rules"
        first.state, first.remote_message_id = OutboxState.CONFIRMED, 501
        await project_rule_page(session, first)
        await session.flush()
        second = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.payload["page_index"].as_integer() == 1)
        )
        assert second is not None
        second.state = OutboxState.UNKNOWN_HOLD
        assert not await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.operation == "send_campaign_card")
        )
        campaign = await session.get(Campaign, cid)
        campaign.state = CampaignState.CANCELLED
        queue_rule_states(session, campaign, state="cancelled")
        queue_rule_states(session, campaign, state="cancelled")
        await session.flush()
        assert (
            await session.scalar(
                select(func.count(TelegramOutbox.id)).where(
                    TelegramOutbox.operation == "edit_campaign_rules"
                )
            )
            == 1
        )
        second.state, second.remote_message_id = OutboxState.CONFIRMED, 502
        await project_rule_page(session, second)
        await session.flush()
        assert (
            await session.scalar(
                select(func.count(TelegramOutbox.id)).where(
                    TelegramOutbox.operation == "edit_campaign_rules"
                )
            )
            == 2
        )
        assert not await session.scalar(
            select(TelegramOutbox.id).where(TelegramOutbox.operation == "send_campaign_card")
        )


async def test_all_pages_confirmed_then_card_keeps_frozen_rules_payload(session_factory):
    cid = await long_campaign(session_factory)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        count = len(campaign.presentation["publication_rule_manifest"][str(FIRST)]["pages"])
        for index in range(count):
            row = await session.scalar(
                select(TelegramOutbox).where(
                    TelegramOutbox.operation == "send_campaign_rules",
                    TelegramOutbox.payload["page_index"].as_integer() == index,
                )
            )
            row.state, row.remote_message_id = OutboxState.CONFIRMED, 501 + index
            await project_rule_page(session, row)
            await session.flush()
        card = await session.scalar(
            select(TelegramOutbox).where(TelegramOutbox.operation == "send_campaign_card")
        )
        assert card is not None and "本群完整规则" in card.payload["rendered_card_text"]
        assert "publication_rule_manifest" not in card.payload["presentation"]
        prizes = list(await session.scalars(select(Prize)))
        assert (
            "publication_card_texts"
            not in campaign_card_payload(campaign, "lottery_test_bot", prizes)["presentation"]
        )
