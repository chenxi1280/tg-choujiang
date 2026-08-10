from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from aiogram.types import User

from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"


class ReferenceQueries:
    def __init__(self) -> None:
        self.reference = None

    async def campaign_by_reference(self, reference):
        self.reference = reference
        return SimpleNamespace(
            id=uuid4(),
            public_ref=PUBLIC_REF,
            presentation={},
        )


class IneligibleParticipants:
    async def begin_registration(self, request):
        return RegistrationAttempt(
            participant_id=uuid4(),
            qualification_check_id=uuid4(),
            required_channel_id=None,
            required_chat_ids=(),
            state=QualificationState.INELIGIBLE,
            requires_observation=False,
        )


class EligibleParticipants:
    def __init__(self) -> None:
        self.deletions = []

    async def begin_registration(self, request):
        return RegistrationAttempt(
            participant_id=uuid4(),
            qualification_check_id=uuid4(),
            required_channel_id=None,
            required_chat_ids=(),
            state=QualificationState.ELIGIBLE,
            requires_observation=False,
        )

    async def schedule_message_deletion(self, **values):
        self.deletions.append(values)


class RecordingMessage:
    def __init__(self) -> None:
        self.reply_markup = None
        self.texts = []
        self.chat = SimpleNamespace(id=201)
        self.message_id = 77

    async def answer(self, text, *, reply_markup=None):
        self.texts.append(text)
        self.reply_markup = reply_markup
        return SimpleNamespace(chat=self.chat, message_id=88)


async def test_verification_callback_uses_campaign_public_reference() -> None:
    queries = ReferenceQueries()
    message = RecordingMessage()
    flow = MemberFlow(
        bot=object(),
        participants=IneligibleParticipants(),
        queries=queries,
        cipher=object(),
    )
    user = User(id=201, is_bot=False, first_name="Tester")

    await flow.register(
        message,
        PUBLIC_REF,
        user=user,
        observed_at=datetime.now(UTC),
    )

    button = message.reply_markup.inline_keyboard[0][0]
    assert queries.reference == PUBLIC_REF
    assert button.callback_data == f"member:verify:{PUBLIC_REF}"


async def test_success_deletion_policy_schedules_command_and_completion() -> None:
    queries = ReferenceQueries()
    campaign_id = uuid4()

    async def campaign_by_reference(reference):
        return SimpleNamespace(
            id=campaign_id,
            public_ref=PUBLIC_REF,
            presentation={
                "delete_trigger_message": True,
                "completion_delete_after_seconds": 20,
            },
        )

    queries.campaign_by_reference = campaign_by_reference
    participants = EligibleParticipants()
    message = RecordingMessage()
    flow = MemberFlow(
        bot=object(),
        participants=participants,
        queries=queries,
        cipher=object(),
    )
    user = User(id=201, is_bot=False, first_name="Tester")

    await flow.register(message, PUBLIC_REF, user=user, observed_at=datetime.now(UTC))

    assert [item["message_id"] for item in participants.deletions] == [77, 88]
    assert participants.deletions[0]["run_at"] < participants.deletions[1]["run_at"]


async def test_rules_remain_visible_after_private_condition_link_is_revoked() -> None:
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        name="测试活动",
        state=CampaignState.DRAWN,
        draw_at=datetime.now(UTC),
        timezone="Asia/Shanghai",
        rules=CampaignRules().model_dump(mode="json"),
        presentation={
            "required_chats": [
                {
                    "id": -100123,
                    "title": "私有参与群",
                    "invite_revoked_at": datetime.now(UTC).isoformat(),
                }
            ]
        },
    )
    queries = SimpleNamespace()
    queries.campaign_by_reference = _async_result(campaign)
    queries.prizes = _async_result((SimpleNamespace(name="一等奖", winner_count=1),))
    message = RecordingMessage()
    flow = MemberFlow(
        bot=object(),
        participants=object(),
        queries=queries,
        cipher=object(),
    )

    await flow.show_rules(message, PUBLIC_REF)

    assert "私有参与群 (加入入口已关闭)" in message.texts[-1]
    assert message.reply_markup is None


def _async_result(value):
    async def result(*_):
        return value

    return result
