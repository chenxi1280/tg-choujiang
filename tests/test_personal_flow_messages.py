from datetime import UTC, datetime
from types import SimpleNamespace

from tests.helpers import seed_campaign
from tg_lottery.application.queries import ParticipatedCampaign, QueryService
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.infrastructure.db import Participant
from tg_lottery.telegram.personal_flow import PersonalFlow


class RecordingMessage:
    def __init__(self) -> None:
        self.from_user = SimpleNamespace(id=202)
        self.answers: list[str] = []
        self.reply_markups: list[object] = []

    async def answer(self, text: str, **values: object) -> None:
        self.answers.append(text)
        self.reply_markups.append(values.get("reply_markup"))


class PersonalQueries:
    async def participated_campaigns(self, _: int) -> list[ParticipatedCampaign]:
        return [
            ParticipatedCampaign(
                public_ref="AbCdEfGhIjKlMnOpQrSt-_",
                name="周末抽奖",
                state=CampaignState.RUNNING,
                qualification=QualificationState.ELIGIBLE,
                tickets=3,
                draw_at=datetime(2026, 8, 8, 3, 25, tzinfo=UTC),
                timezone="Asia/Shanghai",
            )
        ]

    async def created_campaigns(self, _: int, __: str) -> list[object]:
        return [
            SimpleNamespace(
                name="周末抽奖",
                state=CampaignState.RUNNING,
                draw_at=datetime(2026, 8, 8, 3, 25, tzinfo=UTC),
                timezone="Asia/Shanghai",
                public_ref="AbCdEfGhIjKlMnOpQrSt-_",
            )
        ]


async def test_personal_command_ignores_empty_text() -> None:
    message = RecordingMessage()
    flow = PersonalFlow(profiles=None, queries=PersonalQueries(), bot_username="lottery_bot")

    assert await flow.handle_command(message, "") is False
    assert await flow.handle_command(message, "   ") is False
    assert message.answers == []


async def test_personal_lists_render_business_states_and_local_time() -> None:
    message = RecordingMessage()
    flow = PersonalFlow(profiles=None, queries=PersonalQueries(), bot_username="lottery_bot")

    await flow.show_joined(message)
    await flow._show_created(message, "active")

    rendered = "\n".join(message.answers)
    assert "进行中" in rendered
    assert "符合条件" in rendered
    assert "2026年08月08日 11:25 (北京时间)" in rendered
    assert "running" not in rendered
    assert "eligible" not in rendered
    assert "+00:00" not in rendered
    button = message.reply_markups[-1].inline_keyboard[0][0]
    assert button.callback_data == "nav:campaign:AbCdEfGhIjKlMnOpQrSt-_"
    joined_button = message.reply_markups[0].inline_keyboard[0][0]
    assert joined_button.callback_data == "nav:joined:AbCdEfGhIjKlMnOpQrSt-_"


async def test_participated_query_carries_frozen_campaign_timezone(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, _ = await seed_campaign(session)
        campaign.timezone = "Asia/Kuala_Lumpur"
        session.add(
            Participant(
                campaign_id=campaign.id,
                user_id=202,
                display_name="成员二",
                private_reachable=True,
                qualification_state=QualificationState.ELIGIBLE,
                registered_revision=1,
            )
        )

    rows = await QueryService(session_factory).participated_campaigns(202)

    assert len(rows) == 1
    assert rows[0].timezone == "Asia/Kuala_Lumpur"
