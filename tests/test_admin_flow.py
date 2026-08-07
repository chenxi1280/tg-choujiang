from types import SimpleNamespace

import pytest

from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.telegram.admin_flow import AdminFlow


class FakeAdminBot:
    id = 9001

    def __init__(self, *, user_status: str, bot_status: str, can_invite: bool = True) -> None:
        self._user = SimpleNamespace(status=user_status)
        self._bot = SimpleNamespace(status=bot_status, can_invite_users=can_invite)

    async def get_chat_member(self, chat_id: int, user_id: int):
        return self._bot if user_id == self.id else self._user


def _flow(bot: FakeAdminBot) -> AdminFlow:
    return AdminFlow(
        bot=bot,
        bot_username="test_bot",
        sessions=None,
        tokens=None,
        campaigns=None,
        drawing=None,
        winners=None,
        queries=None,
    )


async def test_current_user_and_bot_admin_status_is_required() -> None:
    await _flow(FakeAdminBot(user_status="creator", bot_status="administrator"))._verify_chat_admin(
        -1001, 101
    )
    with pytest.raises(AuthorizationDenied, match="管理员身份无效"):
        await _flow(
            FakeAdminBot(user_status="left", bot_status="administrator")
        )._verify_chat_admin(-1001, 101)
    with pytest.raises(AuthorizationDenied, match="管理员身份无效"):
        await _flow(FakeAdminBot(user_status="creator", bot_status="left"))._verify_chat_admin(
            -1001, 101
        )


async def test_publish_requires_bot_invite_permission() -> None:
    flow = _flow(FakeAdminBot(user_status="creator", bot_status="administrator", can_invite=False))
    with pytest.raises(AuthorizationDenied, match="管理邀请链接权限"):
        await flow._verify_chat_admin(-1001, 101, require_invites=True)
