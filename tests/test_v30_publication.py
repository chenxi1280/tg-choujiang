import pytest
from sqlalchemy import func, select

from tests.v30_helpers import FIRST, START, rules_for, seed_v3
from tg_lottery.application.publication import add_campaign_card_outbox, campaign_card_payload
from tg_lottery.application.rule_publication import project_rule_page, queue_rule_states
from tg_lottery.domain.enums import CampaignState, OutboxState
from tg_lottery.infrastructure.db import Campaign, Prize, TelegramOutbox
from tg_lottery.presentation.mentions import html_utf16_length
from tg_lottery.presentation.participation_rules import condition_blocks, rule_manifest
from tg_lottery.telegram.creator_publication import confirmation_view


@pytest.mark.parametrize("version", ["legacy", "v3.0"])
@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
def test_confirmation_counts_only_effective_bonus_groups(version, bonuses):
    rules = rules_for("invite", bonuses)
    data = rules.model_dump(mode="json") | {"participation_rules_version": version}
    text, _rows = confirmation_view(data)
    for kind, label in (("invite", "邀请"), ("activity", "发言")):
        count = len(data[f"group_{kind}_rules"])
        expected = count if version == "legacy" or kind in bonuses else 0
        assert f"{label}加成\uff1a{expected} 个群开启" in text


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
    assert all(html_utf16_length(page) <= 3500 for page in pages)
    assert all(f"https://t.me/channel_{i}" in "".join(pages) for i in range(50))
    assert "join_abcdefgh" in card and "join_abcdefgh" in "".join(pages)
    assert "每天 2 条" in "".join(pages)
    assert "北京时间自然日" in "".join(pages)
    assert "命令、机器人、服务消息、转发及空白消息不计" in "".join(pages)


@pytest.mark.parametrize("mode", ["message", "invite"])
@pytest.mark.parametrize("bonuses", [(), ("activity",), ("invite",), ("activity", "invite")])
def test_public_counting_explanation_follows_actual_enabled_modules(mode, bonuses):
    rules = rules_for(mode, bonuses)
    rules = rules.model_copy(
        update={"activity": rules.activity.model_copy(update={"duplicate_window_minutes": 11})}
    )
    text = "\n".join(
        condition_blocks(
            dict(
                rules=rules.model_dump(mode="json"),
                presentation={},
                bot_username="lottery_test_bot",
                campaign_ref="abcdefgh",
                cutoff_at=START.isoformat(),
            )
        )
    )
    speech_enabled = mode == "message" or "activity" in bonuses
    assert ("北京时间自然日" in text) is speech_enabled
    assert ("11 分钟内" in text) is speech_enabled
    assert ("登记前不补采" in text) is speech_enabled


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
                for i in range(40)
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
