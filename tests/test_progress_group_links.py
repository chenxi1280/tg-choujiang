from types import SimpleNamespace

from sqlalchemy import select

from tests.test_gate_bonus_reminders import combined_rules
from tests.test_telegram_gateway_messages import FakeCardBot
from tests.v30_helpers import FIRST, SECOND, seed_v3, speech
from tg_lottery.application.progress_group_links import _group_url, progress_group_links
from tg_lottery.infrastructure.db import Campaign, CampaignParticipationGroup, TelegramOutbox
from tg_lottery.presentation.progress_notice import progress_notice_html
from tg_lottery.telegram.gateway import TelegramGateway
from tg_lottery.telegram.gateway_requests import GatewayRequest


async def test_notice_links_identical_group_titles_to_their_own_groups(session_factory):
    cid, _pid = await seed_v3(session_factory, combined_rules(5, 10, two_groups=True))
    title = "同名群 🐈 <测试> & 好友"
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.presentation = {
            "participation_groups": [
                {"id": FIRST, "title": title},
                {"id": SECOND, "title": title},
            ]
        }
        targets = list(
            await session.scalars(
                select(CampaignParticipationGroup).where(
                    CampaignParticipationGroup.campaign_id == cid
                )
            )
        )
        first, second = sorted(targets, key=lambda item: item.position)
        first.username_snapshot = "music_group"
        second.private = True
        second.public_join_url = "https://t.me/+private-secret"
        second.publication_state = "confirmed"
        second.published_message_id = 456
    await speech(session_factory, count=5)
    async with session_factory() as session:
        notice = await session.scalar(select(TelegramOutbox))
    bot = FakeCardBot()
    gateway = TelegramGateway(bot, object(), bot_username="lottery_test_bot")
    await gateway.execute(
        GatewayRequest(notice.operation, notice.recipient_chat_id, notice.payload)
    )
    rendered = bot.sent[0][1][1]
    safe_title = "同名群 🐈 &lt;测试&gt; &amp; 好友"
    assert rendered.count(f'<a href="https://t.me/music_group">{safe_title}</a>') == 2
    assert rendered.count(f'<a href="https://t.me/c/124/456">{safe_title}</a>') == 2
    assert "private-secret" not in rendered
    assert "当前有效抽奖次数" in rendered and "还差 5 条" in rendered
    assert bot.sent[0][2]["parse_mode"] == "HTML"


async def test_missing_direct_link_uses_matching_condition_without_exposing_private_invite(
    session_factory,
):
    cid, _pid = await seed_v3(session_factory, combined_rules(5, 10, two_groups=True))
    async with session_factory() as session, session.begin():
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == cid,
                CampaignParticipationGroup.chat_id_snapshot == SECOND,
            )
        )
        target.private = True
        target.public_join_url = "https://t.me/+private-secret"
    await speech(session_factory, count=5)
    async with session_factory() as session:
        notice = await session.scalar(select(TelegramOutbox))
        campaign = await session.get(Campaign, cid)
    rendered = progress_notice_html(notice.payload, bot_username="lottery_test_bot")
    expected = f"https://t.me/lottery_test_bot?start=condition_{campaign.public_ref}_2"
    assert rendered.count(f'<a href="{expected}">B群</a>') == 2
    assert "private-secret" not in rendered
    assert "B群" in progress_notice_html(notice.payload, bot_username=None)


async def test_group_absent_from_frozen_conditions_does_not_get_a_broken_fallback(session_factory):
    cid, _pid = await seed_v3(session_factory, combined_rules(5, 10, two_groups=True))
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.presentation = {"participation_groups": [{"id": FIRST, "title": "第一群"}]}
        target = await session.scalar(
            select(CampaignParticipationGroup).where(
                CampaignParticipationGroup.campaign_id == cid,
                CampaignParticipationGroup.chat_id_snapshot == SECOND,
            )
        )
        target.private = True
        links = await progress_group_links(session, campaign)
    assert str(SECOND) not in links


def test_migrated_group_does_not_build_message_link_for_a_different_chat():
    target = SimpleNamespace(
        private=True,
        publication_state="confirmed",
        published_message_id=456,
        chat_id_snapshot=-100124,
    )
    assert _group_url(target, -100999) is None


def test_notice_renderer_escapes_text_and_rejects_non_telegram_links():
    payload = {
        "text": "<群> & 标题",
        "group_links": [{"offset": 0, "length": 3, "url": "https://evil.example/group"}],
        "campaign_ref": "abcdefgh",
    }
    assert progress_notice_html(payload, bot_username="bot") == "&lt;群&gt; &amp; 标题"
