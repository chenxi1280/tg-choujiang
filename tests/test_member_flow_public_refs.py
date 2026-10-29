from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from aiogram.types import User

from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_condition_views import human_rule
from tg_lottery.telegram.member_flow import MemberFlow

PUBLIC_REF = "AbCdEfGhIjKlMnOpQrSt-_"


class ReferenceQueries:
    async def participant_campaign_detail(self, user_id, reference):
        return None

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
    async def eligible_participant(self, campaign_id, user_id):
        return None

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

    async def eligible_participant(self, campaign_id, user_id):
        return None

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
        self.chat = SimpleNamespace(id=201, type="private")
        self.message_id = 77

    async def answer(self, text, *, reply_markup=None, parse_mode=None):
        self.texts.append(text)
        self.reply_markup = reply_markup
        return SimpleNamespace(chat=self.chat, message_id=88, reply_markup=reply_markup)


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


async def test_success_deletion_policy_preserves_action_panel() -> None:
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

    assert [item["message_id"] for item in participants.deletions] == [77]
    assert message.texts[0].startswith("✅ 参与成功")
    assert "当前共 1 次抽奖机会" in message.texts[0]


def test_legacy_bonus_rules_have_a_complete_human_readable_description() -> None:
    legacy = CampaignRules.model_validate({"luck_mode": "standard"})

    text = human_rule(legacy)

    assert "每个有效邀请+1次" in text
    assert "None" not in text


async def test_rules_remain_visible_after_private_condition_link_is_revoked() -> None:
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        name="测试活动",
        state=CampaignState.DRAWN,
        preheat_at=datetime.now(UTC),
        cutoff_at=datetime.now(UTC),
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

    assert "私有参与群</a> (加入入口已关闭)" in message.texts[-1]
    button = message.reply_markup.inline_keyboard[0][0]
    assert button.url == f"https://t.me/?start=join_{PUBLIC_REF}"


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
    assert rows[0][0].text == "📡 关注频道"
    assert rows[1][0].callback_data == f"member:condition:{PUBLIC_REF}:2"
    assert "参与条件 1/2" in message.texts[-1]

    await flow.show_condition(message, PUBLIC_REF, 2)

    assert message.reply_markup.inline_keyboard[0][0].text == "👥 加入群聊"
    assert "请加入群聊" in message.texts[-1]
    assert "每天需发送" not in message.texts[-1]


async def test_missing_target_page_lists_multiple_join_links_and_unknown_separately() -> None:
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        cutoff_at=datetime.now(UTC).replace(year=datetime.now(UTC).year + 1),
        presentation={},
    )
    targets = (
        _target("participation_group", -100101, title="参与群 A", chat_type="supergroup"),
        _target("participation_group", -100102, title="参与群 B", chat_type="supergroup"),
        _target("requirement", -100103, title="准入群 C", chat_type="supergroup"),
        _target("requirement", -100104, title="公告频道 D", chat_type="channel"),
    )
    queries = SimpleNamespace(qualification_targets=_async_result(targets))
    flow = MemberFlow(bot=object(), participants=object(), queries=queries, cipher=object())
    observations = (
        {"chat_id": -100101, "state": "eligible"},
        {"chat_id": -100102, "state": "ineligible"},
        {"chat_id": -100103, "state": "ineligible"},
        {"chat_id": -100104, "state": "unknown"},
    )

    text, keyboard = await flow._qualification_reply(
        campaign, QualificationState.INELIGIBLE, observations
    )

    urls = [button.url for row in keyboard.inline_keyboard for button in row if button.url]
    assert urls == ["https://t.me/group_b", "https://t.me/group_c"]
    assert "参与群" in text and "必须全部加入" in text
    assert "准入条件" in text and "必须全部满足" in text
    assert "暂时无法核验" in text and "⏳ 公告频道 D" in text


async def test_missing_target_page_paginates_without_limiting_reverification() -> None:
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        cutoff_at=datetime.now(UTC).replace(year=datetime.now(UTC).year + 1),
        presentation={},
    )
    targets = tuple(
        _target("requirement", -100200 - index, title=f"条件 {index}", chat_type="supergroup")
        for index in range(1, 9)
    )
    observations = tuple({"chat_id": target.chat_id, "state": "ineligible"} for target in targets)
    queries = SimpleNamespace(qualification_targets=_async_result(targets))
    flow = MemberFlow(bot=object(), participants=object(), queries=queries, cipher=object())

    first_text, first_keyboard = await flow._qualification_reply(
        campaign, QualificationState.INELIGIBLE, observations, page=1
    )
    second_text, second_keyboard = await flow._qualification_reply(
        campaign, QualificationState.INELIGIBLE, observations, page=2
    )

    assert "第 1/2 页" in first_text
    first_links = [button for row in first_keyboard.inline_keyboard for button in row if button.url]
    assert len(first_links) == 6
    assert any(
        button.callback_data == f"member:missing:{PUBLIC_REF}:2"
        for row in first_keyboard.inline_keyboard
        for button in row
    )
    assert "第 2/2 页" in second_text
    second_links = [
        button for row in second_keyboard.inline_keyboard for button in row if button.url
    ]
    assert len(second_links) == 2
    assert second_keyboard.inline_keyboard[-1][0].callback_data == f"member:verify:{PUBLIC_REF}"


async def test_waiting_approval_is_visible_and_has_no_duplicate_join_button() -> None:
    campaign = SimpleNamespace(
        id=uuid4(),
        public_ref=PUBLIC_REF,
        cutoff_at=datetime.now(UTC).replace(year=datetime.now(UTC).year + 1),
        presentation={},
    )
    target = _target("requirement", -100301, title="审核群", chat_type="supergroup")
    queries = SimpleNamespace(qualification_targets=_async_result((target,)))
    flow = MemberFlow(bot=object(), participants=object(), queries=queries, cipher=object())

    text, keyboard = await flow._qualification_reply(
        campaign,
        QualificationState.INELIGIBLE,
        (
            {
                "chat_id": target.chat_id,
                "state": "ineligible",
                "observed_status": "join_request_pending",
            },
        ),
    )

    assert "等待管理员批准" in text
    assert "审核群" in text and "等待批准" in text
    assert all(button.url is None for row in keyboard.inline_keyboard for button in row)


def _target(kind: str, chat_id: int, *, title: str, chat_type: str) -> QualificationTarget:
    return QualificationTarget(
        kind=kind,
        record_id=uuid4(),
        chat_id=chat_id,
        title=title,
        chat_type=chat_type,
        private=False,
        public_join_url=f"https://t.me/{'group_b' if chat_id == -100102 else 'group_c'}",
        encrypted_join_url=None,
        revoked_at=None,
        secret_context="test",
    )


def _async_result(value):
    async def result(*_):
        return value

    return result
