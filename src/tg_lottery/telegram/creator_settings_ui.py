from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, Message

from tg_lottery.telegram.creator_renderer import ButtonRows, PanelTarget
from tg_lottery.telegram.creator_schedule import quick_draw_at
from tg_lottery.telegram.draft_config import LOCAL_TIME_FORMAT
from tg_lottery.telegram.public_projection import BEIJING_TIMEZONE, public_timezone_label


def custom_draw_value(timezone: str, *, saved: str = "", now: datetime | None = None) -> str:
    if saved:
        moment = datetime.fromisoformat(saved)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(timezone))
        return moment.astimezone(ZoneInfo(BEIJING_TIMEZONE)).strftime(LOCAL_TIME_FORMAT)
    return quick_draw_at("draft_draw_tomorrow", timezone, now=now).strftime(LOCAL_TIME_FORMAT)


def custom_draw_prompt(timezone: str, *, now: datetime | None = None, saved: str = "") -> str:
    example = custom_draw_value(timezone, saved=saved, now=now)
    label = public_timezone_label(BEIJING_TIMEZONE)
    value_label = "当前时间" if saved else "时间示例"
    return (
        f"🕒 定时开奖 ({label})\n\n{value_label}：\n<code>{example}</code>\n\n"
        "点击上方时间复制，修改后直接发送。\n"
        "格式：年-月-日 时:分；也可选择快捷时间。"
    )


def custom_draw_rows(timezone: str, *, saved: str = "", now: datetime | None = None) -> ButtonRows:
    return [
        [("7天后", "draft_draw_week"), ("10天后", "draft_draw_ten_days")],
        [("明晚20:00", "draft_draw_tomorrow")],
        [("↩ 返回开奖方式", "draft_draw")],
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


def draw_mode_rows() -> ButtonRows:
    return [
        [("🕒 定时开奖 / 自定义时间", "draft_draw_fixed")],
        *_back_rows(),
    ]
