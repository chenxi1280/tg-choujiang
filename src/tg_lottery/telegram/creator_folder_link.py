import re
from typing import Any

from aiogram.types import CallbackQuery, Message

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_participation import participation_rows, participation_text
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data

_FOLDER_LINK = re.compile(r"https://t\.me/addlist/[A-Za-z0-9_-]+")


def validated_folder_link(value: str) -> str:
    value = value.strip()
    if not _FOLDER_LINK.fullmatch(value):
        raise UserFacingError("请发送 Telegram 组合分享链接，格式为 https://t.me/addlist/…")
    return value


class CreatorFolderLink:
    def __init__(self, *, sessions: Any, renderer: Any) -> None:
        self._sessions = sessions
        self._renderer = renderer

    async def show(self, query: CallbackQuery, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        await self._sessions.set(query.from_user.id, "draft:folder_link", data)
        current = "已设置" if data.get("folder_link") else "未设置"
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text=(
                f"🗂 组合分享链接（选填）\n\n当前：{current}\n\n"
                "用于一次加入多个群和频道。链接中的列表不替代活动的准入条件，"
                "参与时仍会逐项核验。\n\n"
                "在 Telegram 的设置 → 聊天文件夹中创建文件夹，"
                "选择需要分享的群和频道后创建邀请链接。\n"
                "请发送 https://t.me/addlist/… 格式的链接。"
            ),
            rows=[
                [("清除组合链接", "draft_folder_clear")],
                [("↩ 返回条件列表", "draft_participation")],
            ],
        )

    async def clear(self, query: CallbackQuery, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        await self._save(query.from_user.id, query.message.chat.id, data | {"folder_link": ""})

    async def receive(self, message: Message, data: dict[str, Any]) -> None:
        link = validated_folder_link(message.text or "")
        await self._save(message.from_user.id, message.chat.id, data | {"folder_link": link})

    async def _save(self, user_id: int, chat_id: int, data: dict[str, Any]) -> None:
        await self._sessions.set(user_id, "draft:participation", data)
        await self._renderer.edit(
            PanelTarget(user_id, chat_id, int(data["panel_message_id"])),
            text=participation_text(data),
            rows=participation_rows(data),
        )
