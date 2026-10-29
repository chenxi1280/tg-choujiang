from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.helpers import seed_campaign
from tests.test_multi_prize_draw import _seed_multi_prize_case
from tests.v30_helpers import rules_for
from tg_lottery.application.campaigns import CampaignService
from tg_lottery.application.drawing import DrawingService
from tg_lottery.application.publication import campaign_card_payload
from tg_lottery.application.settlement import SettlementService
from tg_lottery.infrastructure.db import Campaign, Prize, TelegramOutbox
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.campaign_card import card_time
from tg_lottery.presentation.campaign_state_times import campaign_state_times
from tg_lottery.presentation.mentions import CAPTION_UTF16_LIMIT, html_utf16_length
from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.telegram.gateway import TelegramGateway, _campaign_state_card_text
from tg_lottery.telegram.gateway_requests import GatewayRequest

ORIGINAL_CUTOFF = datetime(2026, 10, 29, 12, 9, 48, tzinfo=UTC)
EARLY_CUTOFF = datetime(2026, 10, 29, 11, 39, 49, tzinfo=UTC)


class CapturingEditBot:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))
        return True

    async def edit_message_caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))
        return True


@pytest.mark.parametrize("has_cover", [False, True])
async def test_early_cutoff_updates_card_and_rule_page_without_changing_frozen_text(
    session_factory, has_cover
):
    async with session_factory() as session, session.begin():
        group, campaign, _ = await seed_campaign(
            session, rules=rules_for("message"), cutoff_at=ORIGINAL_CUTOFF
        )
        campaign.draw_at = ORIGINAL_CUTOFF + timedelta(minutes=5)
        campaign.published_message_id = 321
        campaign.presentation = {"cover_file_id": "photo"} if has_cover else {}
        prizes = list(await session.scalars(select(Prize).where(Prize.campaign_id == campaign.id)))
        frozen, _ = rule_manifest(campaign_card_payload(campaign, "lottery_test_bot", prizes))
        campaign.presentation = campaign.presentation | {
            "publication_card_texts": {str(group.chat_id): frozen},
            "publication_rule_manifest": {
                str(group.chat_id): {"pages": [frozen], "remote_ids": {"0": 322}}
            },
        }
        campaign_id, chat_id = campaign.id, group.chat_id
        original_rules = campaign.rules

    service = CampaignService(partial(UnitOfWork, session_factory))
    await service.early_cutoff(
        campaign_id, actor_user_id=101, cutoff_at=EARLY_CUTOFF, reason="提前截止测试"
    )
    async with session_factory() as session:
        stored = await session.get(Campaign, campaign_id)
        rows = tuple(
            await session.scalars(
                select(TelegramOutbox).where(TelegramOutbox.aggregate_id == str(campaign_id))
            )
        )
        assert stored.presentation["publication_card_texts"][str(chat_id)] == frozen
        assert stored.presentation["publication_rule_manifest"][str(chat_id)]["pages"] == [frozen]
        assert stored.rules == original_rules
    card = next(row for row in rows if row.operation == "edit_campaign_card_state")
    rule_page = next(row for row in rows if row.operation == "edit_campaign_rules")
    bot = CapturingEditBot()
    gateway = TelegramGateway(bot, object())
    await gateway.execute(GatewayRequest(card.operation, chat_id, card.payload))
    await gateway.execute(GatewayRequest(rule_page.operation, chat_id, rule_page.payload))

    card_text = bot.calls[0][2]["caption"] if has_cover else bot.calls[0][1][0]
    rule_text = bot.calls[1][1][0]
    for text in (card_text, rule_text):
        assert "实际截止\uff1a2026年10月29日 19时39分49秒" in text
        assert "计划开奖\uff1a2026年10月29日 19时44分50秒" in text
        assert "原计划" in text
        assert "2026年10月29日 20时09分48秒" in text
        assert "2026年10月29日 20时14分48秒" in text


async def test_drawn_card_reports_completed_time_from_saved_draw_run(session_factory):
    now = datetime.now(UTC)
    campaign_id = await _seed_multi_prize_case(session_factory, now)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, campaign_id)
        campaign.presentation = {"publication_card_texts": {"-100123": "冻结的公告规则"}}
    factory = partial(UnitOfWork, session_factory)
    await SettlementService(factory).settle(campaign_id, now)
    draw = await DrawingService(factory).run(campaign_id, actor_user_id=101)
    async with session_factory() as session:
        card = await session.scalar(
            select(TelegramOutbox).where(
                TelegramOutbox.aggregate_id == str(campaign_id),
                TelegramOutbox.operation == "edit_campaign_card_state",
            )
        )
    text = _campaign_state_card_text(card.payload)
    assert f"实际开奖\uff1a{card_time(draw.completed_at.isoformat(), 'Asia/Shanghai')}" in text
    assert "计划开奖\uff1a" not in text
    assert "冻结的公告规则" in text
    assert "🏆 中奖者" in text


def test_covered_card_keeps_times_and_frozen_rules_when_winner_summary_will_not_fit():
    frozen = "规" * 900
    summary = campaign_state_times(
        cutoff_at=EARLY_CUTOFF, draw_at=EARLY_CUTOFF + timedelta(minutes=5), timezone="UTC"
    )
    text = _campaign_state_card_text(
        {
            "campaign_name": "测试",
            "card_state": "drawn",
            "has_cover": True,
            "time_summary": summary,
            "frozen_rules_text": frozen,
            "winners": [{"user_id": 201, "display_name": "中奖人" * 100}],
        }
    )
    assert "实际截止\uff1a2026年10月29日 19时39分49秒" in text
    assert frozen in text
    assert html_utf16_length(text) <= CAPTION_UTF16_LIMIT
