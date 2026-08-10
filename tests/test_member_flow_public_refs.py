from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from aiogram.types import User

from tg_lottery.application.participants import RegistrationAttempt
from tg_lottery.domain.enums import QualificationState
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


class RecordingMessage:
    def __init__(self) -> None:
        self.reply_markup = None

    async def answer(self, text, *, reply_markup=None):
        self.reply_markup = reply_markup


async def test_verification_callback_uses_campaign_public_reference() -> None:
    queries = ReferenceQueries()
    message = RecordingMessage()
    flow = MemberFlow(
        bot=object(),
        participants=IneligibleParticipants(),
        queries=queries,
        campaigns=object(),
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
