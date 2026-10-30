"""Handle explicit UI edit outcomes without replaying completed business work."""

from aiogram.exceptions import TelegramBadRequest

from tg_lottery.domain.errors import UserFacingError


class PanelUnavailableError(UserFacingError):
    def __init__(self):
        super().__init__(
            "原页面已删除或无法更新，页面显示未完成。已保存的数据保留；"
            "请发送 /首页 打开首页，再继续草稿或查看原活动。"
        )


def unchanged_edit(error: TelegramBadRequest) -> bool:
    return error.message.startswith("Bad Request: message is not modified:")


async def idempotent_edit(operation):
    """Return None only when Telegram explicitly confirms identical content/markup."""
    try:
        return await operation
    except TelegramBadRequest as exc:
        if not unchanged_edit(exc):
            raise
        return None


async def _panel_operation(operation):
    try:
        return await idempotent_edit(operation)
    except TelegramBadRequest as exc:
        if exc.message in {
            "Bad Request: message to edit not found",
            "Bad Request: message can't be edited",
            "Bad Request: there is no text in the message to edit",
        }:
            raise PanelUnavailableError() from exc
        raise


async def edit_panel(message, text: str, **options) -> None:
    await _panel_operation(message.edit_text(text, **options))


async def edit_panel_at(bot, text: str, *, chat_id: int, message_id: int, **options) -> None:
    await _panel_operation(
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, **options)
    )


async def edit_panel_markup(message, **options) -> None:
    await _panel_operation(message.edit_reply_markup(**options))
