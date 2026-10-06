from collections.abc import Mapping

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.public_projection import format_public_time


def home_keyboard(data: Mapping[str, object] | None = None) -> InlineKeyboardMarkup:
    label, action = creation_entry(data)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="我参加的抽奖", callback_data="nav:join"),
                InlineKeyboardButton(text="我发起的抽奖", callback_data="nav:created"),
            ],
            [InlineKeyboardButton(text=label, callback_data=action)],
            [
                InlineKeyboardButton(text="使用指南", callback_data="nav:help"),
                InlineKeyboardButton(text="个人设置", callback_data="nav:settings"),
            ],
        ]
    )


def creation_entry(data: Mapping[str, object] | None) -> tuple[str, str]:
    if data and data.get("published_campaign_id"):
        return "查看发布状态", "nav:create"
    if data:
        return "继续创建抽奖", "nav:create"
    return "创建抽奖", "nav:create"


def home_text(data: Mapping[str, object] | None = None) -> str:
    text = "抽奖助手\n\n参加活动、发起抽奖和本人设置，请选择下方操作。"
    if data and data.get("published_campaign_id"):
        return text + "\n\n上次活动已创建，请查看同一活动的发布状态。"
    if data:
        text += f"\n\n可继续草稿：{data.get('title') or '未命名抽奖'}"
        if data.get("draft_expires_at"):
            text += "\n保留到：" + format_public_time(
                str(data["draft_expires_at"]), "Asia/Shanghai"
            )
        return text
    return text


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="设置归属地区", callback_data="nav:region")],
            [InlineKeyboardButton(text="查看我的用户ID", callback_data="nav:myid")],
            [InlineKeyboardButton(text="返回首页", callback_data="nav:index")],
        ]
    )
