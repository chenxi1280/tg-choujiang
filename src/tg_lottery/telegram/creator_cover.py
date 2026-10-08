from typing import Any

from aiogram.types import CallbackQuery, Message

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_renderer import ButtonRows, CreatorRenderer, PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data
from tg_lottery.telegram.creator_sections import enter_section
from tg_lottery.telegram.creator_settings_ui import _message, _user_id


def cover_rows(*, has_cover: bool) -> ButtonRows:
    actions = [[("清除封面", "draft_cover_clear")]] if has_cover else []
    return [*actions, [("✅ 完成", "draft_back")]]


def cover_prompt_text(*, has_cover: bool) -> str:
    if has_cover:
        return (
            "🖼️ 当前已设置封面。\n直接发送新图片即可替换；点「✅ 完成」保留当前封面返回封面与说明。"
        )
    return "📤 请发送一张图片作为抽奖封面："


class CreatorCover:
    def __init__(self, *, sessions: AdminSessionService, renderer: CreatorRenderer) -> None:
        self._sessions = sessions
        self._renderer = renderer

    async def show(self, query: CallbackQuery, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        updated = enter_section(data, "cover") | {
            "panel_message_id": _message(query).message_id,
            "guided": False,
        }
        await self._sessions.set(query.from_user.id, "draft:cover", updated)
        await self._renderer.render(
            _message(query),
            user_id=query.from_user.id,
            text=cover_prompt_text(has_cover=bool(updated.get("cover_file_id"))),
            rows=cover_rows(has_cover=bool(updated.get("cover_file_id"))),
        )

    async def receive(self, message: Message, data: dict[str, Any]) -> None:
        if not message.photo:
            raise UserFacingError("封面必须发送 Telegram 图片")
        user_id = _user_id(message)
        replaced = bool(data.get("cover_file_id"))
        updated = enter_section(data, "cover") | {"cover_file_id": message.photo[-1].file_id}
        await self._sessions.set(user_id, "draft:cover", updated)
        status = "图片替换成功" if replaced else "图片上传成功"
        text = (
            f"✅ {status}，已保存为抽奖封面。\n\n"
            "可以直接再发一张图片替换，或点「✅ 完成」返回封面与说明。"
        )
        panel = await message.answer(text)
        # Bind the session to the new panel *before* minting its buttons: the token
        # carrying this panel id is what makes the buttons below valid.
        await self._sessions.set(
            user_id, "draft:cover", updated | {"panel_message_id": panel.message_id}
        )
        keyboard = await self._renderer.keyboard_for(
            PanelTarget(user_id, panel.chat.id, panel.message_id),
            cover_rows(has_cover=True),
        )
        await panel.edit_reply_markup(reply_markup=keyboard)
