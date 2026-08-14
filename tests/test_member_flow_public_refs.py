from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from aiogram.types import User

from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_flow import MemberFlow, _human_rule, _registration_success_text

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
            rules=CampaignRules().model_dump(mode="json"),
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
            rules=CampaignRules().model_dump(mode="json"),
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
    assert message.texts[0] == "报名成功。"


def test_success_message_only_promises_an_invite_link_when_enabled() -> None:
    assert _registration_success_text(CampaignRules()) == "报名成功。"
    enabled = CampaignRules(bonus_types=("invite",))
    assert "专属邀请链接" in _registration_success_text(enabled)


def test_legacy_bonus_rules_have_a_complete_human_readable_description() -> None:
    legacy = CampaignRules.model_validate({"luck_mode": "standard"})

    text = _human_rule(legacy)

    assert "每个有效邀请+1次" in text
    assert "None" not in text


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
    button = message.reply_markup.inline_keyboard[0][0]
    assert button.callback_data == f"member:condition:{PUBLIC_REF}:1"


async def test_condition_page_distinguishes_channel_and_supports_navigation() -> None:
    rules = CampaignRules(bonus_types=("activity",))
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        rules=rules.model_dump(mode="json"),
        presentation={
            "required_chats": [
                {
                    "id": -100123,
                    "title": "公告频道",
                    "type": "channel",
                    "join_url": "https://t.me/notices",
                },
                {
                    "id": -100124,
                    "title": "讨论群",
                    "type": "supergroup",
                    "join_url": "https://t.me/discussion",
                },
            ]
        },
    )
    queries = SimpleNamespace(campaign_by_reference=_async_result(campaign))
    message = RecordingMessage()
    flow = MemberFlow(bot=object(), participants=object(), queries=queries, cipher=object())

    await flow.show_condition(message, PUBLIC_REF, 1)

    rows = message.reply_markup.inline_keyboard
    assert rows[0][0].text == "关注频道"
    assert rows[1][0].callback_data == f"member:condition:{PUBLIC_REF}:2"
    assert "参与条件 1/2" in message.texts[-1]

    await flow.show_condition(message, PUBLIC_REF, 2)

    assert "本群每天需发送 10 条有效消息" in message.texts[-1]


def _async_result(value):
    async def result(*_):
        return value

    return result
