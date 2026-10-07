import logging

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)

from tg_lottery.domain.errors import UserFacingError


async def send_qualification_detail(bot, *, user_id, text, keyboard, callback):
    from tg_lottery.telegram.member_callbacks import safe_answer

    try:
        await bot.send_message(user_id, text, reply_markup=keyboard)
    except TelegramAPIError as error:
        logging.getLogger(__name__).warning(
            "Qualification detail private delivery failed",
            extra={"user_id": user_id, "error_type": type(error).__name__},
            exc_info=True,
        )
        text = _private_failure_text(error)
        if callback is None:
            raise UserFacingError(text) from error
        await safe_answer(callback, text, alert=True)
        return
    if callback is not None:
        await safe_answer(callback, "尚未满足参与条件，详情已发送到本人私聊", alert=True)


def _private_failure_text(error):
    if isinstance(error, TelegramForbiddenError):
        return "尚未满足参与条件；私聊不可达，请先打开机器人并点击开始，或解除屏蔽后查看详情。"
    if isinstance(error, (TelegramBadRequest, TelegramUnauthorizedError, TelegramRetryAfter)):
        return "尚未满足参与条件；私聊详情发送失败，请稍后在私聊中查看或联系活动管理员。"
    return "尚未满足参与条件；私聊详情发送结果未确认，请在机器人私聊中查看或稍后核查。"
