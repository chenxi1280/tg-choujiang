from datetime import datetime
from typing import Any

from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardButton, Message

from tg_lottery.telegram.creator_renderer import ButtonRows, PanelTarget
from tg_lottery.telegram.creator_schedule import quick_draw_at
from tg_lottery.telegram.draft_config import LOCAL_TIME_FORMAT
from tg_lottery.telegram.public_projection import BEIJING_TIMEZONE, public_timezone_label


def custom_draw_prompt(timezone: str, *, now: datetime | None = None) -> str:
    example = quick_draw_at("draft_draw_tomorrow", timezone, now=now)
    label = public_timezone_label(BEIJING_TIMEZONE)
    return f"🕒 回复开奖时间 ({label})\n\n例如: {example.strftime(LOCAL_TIME_FORMAT)}"


def custom_draw_rows(timezone: str) -> ButtonRows:
    example = quick_draw_at("draft_draw_tomorrow", timezone).strftime(LOCAL_TIME_FORMAT)
    return [
        [InlineKeyboardButton(text="复制时间示例", copy_text=CopyTextButton(text=example))],
        *_back_rows(),
    ]


def _message(query: CallbackQuery) -> Message:
    if not isinstance(query.message, Message):
        raise ValueError("callback message is no longer accessible")
    return query.message


def _user_id(message: Message) -> int:
    return _required_user(message).id


def _required_user(message: Message):
    if message.from_user is None:
        raise ValueError("无法识别当前用户")
    return message.from_user


def _target(message: Message, data: dict[str, Any]) -> PanelTarget:
    return PanelTarget(_user_id(message), message.chat.id, int(data["panel_message_id"]))


def _back_rows() -> ButtonRows:
    return [[("↩ 返回上一层", "draft_back")]]


def _draw_rows() -> ButtonRows:
    return [
        [("1小时后", "draft_draw_hour"), ("今晚20:00", "draft_draw_evening")],
        [("明晚20:00", "draft_draw_tomorrow"), ("自定义时间", "draft_draw_custom")],
        *_back_rows(),
    ]


def draw_mode_rows() -> ButtonRows:
    return [
        [("自定义时间（北京时间）", "draft_draw_custom")],
        [("满人开奖", "draft_draw_full"), ("快捷时间", "draft_draw_fixed")],
        *_back_rows(),
    ]
