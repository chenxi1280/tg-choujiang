from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from tests.helpers import seed_campaign
from tg_lottery.application.queries import QueryService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.telegram.member_flow import MemberFlow, member_keyboard


class RecordingMessage:
    def __init__(self) -> None:
        self.text = ""
        self.markup = None

    async def answer(self, text: str, **values: object) -> None:
        self.text = text
        self.markup = values.get("reply_markup")


class InviteQueries:
    def __init__(self, campaign: object, link: object) -> None:
        self._campaign = campaign
        self._link = link

    async def campaign_by_reference(self, reference):
        assert reference == self._campaign.public_ref
        return self._campaign

    async def invite_link_for_participant(self, campaign_id, user_id):
        assert campaign_id == self._campaign.id
        assert user_id == 201
        return self._link


class PlainCipher:
    def decrypt(self, encrypted, *, context):
        assert encrypted == b"encrypted"
        assert context.endswith(str(self.link_id))
        return "https://t.me/+campaign_personal"


async def test_member_can_retrieve_a_campaign_scoped_personal_invite_link() -> None:
    now = datetime.now(UTC)
    campaign = _campaign(now + timedelta(hours=25))
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"encrypted")
    cipher = PlainCipher()
    cipher.link_id = link.id
    flow = MemberFlow(
        bot=object(),
        participants=object(),
        queries=InviteQueries(campaign, link),
        cipher=cipher,
    )
    message = RecordingMessage()

    await flow.show_invite_link(message, campaign.public_ref, user_id=201)

    assert "最晚计奖入群" in message.text
    assert "满足留群及活动条件" in message.text
    assert message.markup.inline_keyboard[0][0].copy_text.text == "https://t.me/+campaign_personal"
    assert message.markup.inline_keyboard[1][0].url == "https://t.me/+campaign_personal"
    assert "share/url" in message.markup.inline_keyboard[2][0].url


async def test_legacy_invite_link_after_join_deadline_still_opens_without_bonus_promise() -> None:
    campaign = _campaign(datetime.now(UTC) + timedelta(hours=1))
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"encrypted")
    cipher = PlainCipher()
    cipher.link_id = link.id
    flow = MemberFlow(
        bot=object(), participants=object(), queries=InviteQueries(campaign, link), cipher=cipher
    )
    message = RecordingMessage()
    await flow.show_invite_link(message, campaign.public_ref, user_id=201)
    assert "最晚计奖入群时间已过" in message.text
    assert "新入群不计加成" in message.text
    assert "仅核验此前邀请" in message.text
    assert message.markup.inline_keyboard[0][0].text == "复制入群链接"
    assert message.markup.inline_keyboard[2][0].text == "转发入群"


async def test_legacy_invite_link_before_preheat_does_not_say_campaign_has_ended() -> None:
    now = datetime.now(UTC)
    campaign = _campaign(now + timedelta(days=2))
    campaign.state = CampaignState.SCHEDULED
    campaign.preheat_at = now + timedelta(hours=1)
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"encrypted")
    cipher = PlainCipher()
    cipher.link_id = link.id
    flow = MemberFlow(
        bot=object(), participants=object(), queries=InviteQueries(campaign, link), cipher=cipher
    )
    message = RecordingMessage()
    await flow.show_invite_link(message, campaign.public_ref, user_id=201)
    assert "邀请加成尚未开始" in message.text
    assert "已截止" not in message.text
    assert message.markup.inline_keyboard[0][0].text == "复制入群链接"


async def test_expired_campaign_link_still_opens_but_never_counts_again() -> None:
    campaign = _campaign(datetime.now(UTC) - timedelta(minutes=1))
    campaign.state = CampaignState.DRAWN
    link = SimpleNamespace(id=uuid4(), encrypted_url=b"encrypted")
    cipher = PlainCipher()
    cipher.link_id = link.id
    flow = MemberFlow(
        bot=object(),
        participants=object(),
        queries=InviteQueries(campaign, link),
        cipher=cipher,
    )
    message = RecordingMessage()

    await flow.show_invite_link(message, campaign.public_ref, user_id=201)

    assert "仍有效的旧链接可入群" in message.text
    assert "不再计算本期或以后任何抽奖加成" in message.text
    assert "邀请入口已隐藏" in message.text
    buttons = [button for row in message.markup.inline_keyboard for button in row]
    assert not any(button.url or button.copy_text for button in buttons)
    assert "邀请记录" in [button.text for button in buttons]


def test_registration_keyboard_exposes_only_the_current_campaign_link() -> None:
    keyboard = member_keyboard("AbCdEfGhIjKlMnOpQrSt-_", True)
    buttons = [button for row in keyboard.inline_keyboard for button in row]

    invite = next(button for button in buttons if button.text == "我的加成")
    assert invite.callback_data == "member:bonus:AbCdEfGhIjKlMnOpQrSt-_"
    tickets = next(button for button in buttons if button.text == "我的抽奖次数")
    records = next(button for button in buttons if button.text == "邀请记录")
    assert tickets.callback_data == "member:tickets:AbCdEfGhIjKlMnOpQrSt-_"
    assert records.callback_data == "member:invites:AbCdEfGhIjKlMnOpQrSt-_:0"


async def test_invite_link_query_requires_matching_campaign_and_user(session_factory) -> None:
    async with session_factory() as session, session.begin():
        _, campaign, participant = await seed_campaign(session)
        campaign_id = campaign.id
        link = InviteLink(
            campaign_id=campaign.id,
            participant_id=participant.id,
            link_hash="b" * 64,
            encrypted_url=b"encrypted",
            telegram_name="lot_scoped",
            expire_at=campaign.cutoff_at,
        )
        session.add(link)
        await session.flush()
        link_id = link.id
    queries = QueryService(session_factory)

    assert (await queries.invite_link_for_participant(campaign_id, 201)).id == link_id
    assert await queries.invite_link_for_participant(campaign_id, 999) is None


def _campaign(cutoff_at: datetime):
    rules = CampaignRules(bonus_types=("invite",))
    return SimpleNamespace(
        id=uuid4(),
        public_ref="AbCdEfGhIjKlMnOpQrSt-_",
        name="本期抽奖",
        state=CampaignState.RUNNING,
        preheat_at=cutoff_at - timedelta(days=2),
        cutoff_at=cutoff_at,
        timezone="Asia/Shanghai",
        rules=rules.model_dump(mode="json"),
    )
