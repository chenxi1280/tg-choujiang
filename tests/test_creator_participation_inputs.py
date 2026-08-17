from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetChat, GetChatMember

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_participation import message_chat_refs
from tg_lottery.telegram.required_chats import resolve_required_chat


class RequiredChatBot:
    id = 9001

    def __init__(
        self,
        *,
        get_chat_error: bool = False,
        member_error: bool = False,
        username: str | None = "test_channel",
        can_invite_users: bool = True,
    ) -> None:
        self._get_chat_error = get_chat_error
        self._member_error = member_error
        self._username = username
        self._can_invite_users = can_invite_users

    async def get_chat(self, ref: int | str):
        if self._get_chat_error:
            raise TelegramBadRequest(method=GetChat(chat_id=ref), message="chat not found")
        return SimpleNamespace(
            id=-100123,
            title="测试频道",
            full_name=None,
            type="channel",
            username=self._username,
            permissions=None,
        )

    async def get_chat_member(self, chat_id: int, user_id: int):
        if self._member_error:
            raise TelegramBadRequest(
                method=GetChatMember(chat_id=chat_id, user_id=user_id),
                message="participant_id_invalid",
            )
        return SimpleNamespace(
            status="administrator",
            can_invite_users=self._can_invite_users,
        )


def test_forwarded_channel_origin_is_used_instead_of_post_text() -> None:
    origin = SimpleNamespace(chat=SimpleNamespace(id=-100123), sender_chat=None)
    message = _message("帖子正文不是群链接", forward_origin=origin)

    assert message_chat_refs(message) == [-100123]


def test_forwarded_sender_chat_origin_is_supported() -> None:
    origin = SimpleNamespace(chat=None, sender_chat=SimpleNamespace(id=-100234))
    message = _message("匿名管理员消息", forward_origin=origin)

    assert message_chat_refs(message) == [-100234]


def test_legacy_forwarded_chat_origin_is_supported() -> None:
    message = _message(None, forward_from_chat=SimpleNamespace(id=-100456))

    assert message_chat_refs(message) == [-100456]


def test_forward_without_a_chat_source_has_an_actionable_error() -> None:
    message = _message(
        "普通用户转发正文",
        forward_origin=SimpleNamespace(user=SimpleNamespace(id=201)),
    )

    with pytest.raises(UserFacingError, match="没有携带群聊或频道来源"):
        message_chat_refs(message)


def test_caption_link_is_accepted_for_media_messages() -> None:
    message = _message(None, caption="https://t.me/test_channel")

    assert message_chat_refs(message) == ["@test_channel"]


async def test_get_chat_failure_names_the_unreachable_reference() -> None:
    with pytest.raises(UserFacingError, match=r"@missing.*加入该群或频道"):
        await resolve_required_chat(RequiredChatBot(get_chat_error=True), "@missing")


async def test_get_chat_member_failure_names_the_resolved_chat() -> None:
    with pytest.raises(UserFacingError, match=r"测试频道.*设为管理员"):
        await resolve_required_chat(RequiredChatBot(member_error=True), "@test_channel")


async def test_public_chat_does_not_require_invite_link_permission() -> None:
    record = await resolve_required_chat(
        RequiredChatBot(can_invite_users=False),
        "@test_channel",
    )

    assert record["private"] is False


async def test_private_chat_requires_invite_link_permission() -> None:
    bot = RequiredChatBot(username=None, can_invite_users=False)

    with pytest.raises(UserFacingError, match=r"测试频道.*邀请用户权限"):
        await resolve_required_chat(bot, -100123)


def _message(text: str | None, **values: object) -> SimpleNamespace:
    defaults = {
        "text": text,
        "caption": None,
        "forward_origin": None,
        "forward_from_chat": None,
    }
    return SimpleNamespace(**(defaults | values))
