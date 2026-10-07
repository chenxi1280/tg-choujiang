from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from tests.test_v32_member_navigation import _detail
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow
from tg_lottery.telegram.member_navigation import MemberNavigation
from tg_lottery.telegram.update_handler import LotteryUpdateHandler

REFERENCE = "AbCdEfGhIjKlMnOpQrSt-_"
USER_ID = 202
CHAT_ID = -100123


def campaign(*, bonuses=("activity",)):
    now = datetime.now(UTC)
    rules = CampaignRules(
        participation_rules_version="v3.0",
        admission={"type": "message", "groups": {str(CHAT_ID): {"daily_messages": 2}}},
        bonus_types=bonuses,
        group_activity_rules={str(CHAT_ID): {"messages_per_cycle": 2}},
    )
    return SimpleNamespace(
        id=uuid4(),
        public_ref=REFERENCE,
        name="提醒回归",
        state=CampaignState.RUNNING,
        rules=rules.model_dump(mode="json"),
        preheat_at=now - timedelta(hours=1),
        cutoff_at=now + timedelta(days=1),
        draw_at=now + timedelta(days=1, minutes=5),
        timezone="Asia/Shanghai",
        presentation={},
    )


def queries_for(record, *, registered=True):
    return SimpleNamespace(
        campaign_by_reference=AsyncMock(return_value=record),
        participant_campaign_detail=AsyncMock(return_value=_detail() if registered else None),
        qualification_targets=AsyncMock(return_value=()),
        group_invite_link_statuses=AsyncMock(return_value={}),
        group_bonus_progress=AsyncMock(
            return_value=SimpleNamespace(base_progress="历史进度", invitations=(), activities=())
        ),
    )


def flow_for(queries, *, participants=None):
    bot = SimpleNamespace(
        id=99,
        send_message=AsyncMock(),
        get_chat=AsyncMock(
            return_value=SimpleNamespace(permissions=SimpleNamespace(can_send_messages=True))
        ),
        get_chat_member=AsyncMock(
            return_value=SimpleNamespace(status="administrator", can_invite_users=True)
        ),
    )
    return MemberFlow(
        bot=bot,
        queries=queries,
        participants=participants or SimpleNamespace(queue_temporary_reminder=AsyncMock()),
        cipher=object(),
        bot_username="test_bot",
    )


def handler_for(flow):
    async def issue(**options):
        return tuple(f"token{index}" for index, _ in enumerate(options["requests"]))

    nav = MemberNavigation(
        bot=flow._bot,
        queries=flow._queries,
        cipher=flow._cipher,
        tokens=SimpleNamespace(create_many=AsyncMock(side_effect=issue)),
        profiles=object(),
        bot_username="test_bot",
    )
    return LotteryUpdateHandler(
        bot=flow._bot,
        default_timezone="Asia/Shanghai",
        member_flow=flow,
        navigation=nav,
        admin_flow=SimpleNamespace(suspend_draft=AsyncMock()),
        personal_flow=SimpleNamespace(handle_callback=AsyncMock(return_value=False)),
        tokens=object(),
        groups=object(),
        activity=object(),
        invitations=object(),
        queries=flow._queries,
        stats=object(),
    )
