from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_base_activity import base_rules_text
from tg_lottery.telegram.creator_behavior_views import admission_summary
from tg_lottery.telegram.creator_bonus import bonus_summary
from tg_lottery.telegram.creator_bonus_warnings import bonus_window_warning
from tg_lottery.telegram.creator_invite_retention import summary as retention_summary
from tg_lottery.telegram.creator_messages import message_policy_summary
from tg_lottery.telegram.draft_config import campaign_input, missing_fields
from tg_lottery.telegram.public_projection import format_public_time

ButtonRows = list[list[tuple[str, str]]]


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 抽奖管理", callback_data="nav:index")],
            [InlineKeyboardButton(text="创建/继续抽奖", callback_data="nav:create")],
            [InlineKeyboardButton(text="我参加的抽奖", callback_data="nav:join")],
            [InlineKeyboardButton(text="设置归属地区", callback_data="nav:region")],
            [InlineKeyboardButton(text="📖 使用指南", callback_data="nav:help")],
        ]
    )


def preview_text(data: dict[str, Any], *, now: datetime | None = None) -> str:
    status = _preview_status(data, now or utcnow())
    return (
        "🎰 抽奖预览\n\n"
        f"🎰 抽奖标题：{data.get('title') or '【等待设置】'}\n\n"
        f"🏞️ 封面设置：{'【已设置】' if data.get('cover_file_id') else '【等待设置】'}\n\n"
        f"🪪 用户限制：{_user_limits(data)}\n\n"
        f"🌏️ 地区限制：{_region_limits(data)}\n\n"
        f"👥 参与活动的群：{_participation_groups(data)}\n"
        f"📮 准入条件：{_requirements(data)}\n\n"
        f"{_base_summary(data)}\n\n"
        f"🎁 奖品内容：\n{_prize_lines(data.get('prizes', []))}\n"
        f"⚠️ 抽奖说明：{data.get('description') or '【等待设置】'}\n\n"
        f"🔋 抽奖加成：{bonus_summary(data)}\n{retention_summary(data)}\n"
        f"🌀 开奖方式：{_draw_text(data)}\n\n"
        f"🎅 发奖人员：{data.get('issuer_name') or data.get('creator_name') or '当前管理员'}\n\n"
        f"🔔 参与提示：{message_policy_summary(data)}\n\n"
        f"当前状态：{status}\n{bonus_window_warning(data, now=now)}"
        "（本页面为预览文案，不是正式效果）"
    )


def _preview_status(data: dict[str, Any], now: datetime) -> str:
    missing = missing_fields(data)
    if missing:
        return "还需设置：" + "、".join(missing)
    try:
        campaign_input(data, now=now)
    except UserFacingError as exc:
        return "需要调整：" + exc.public_message
    return "可以发布"


def dashboard_rows(data: dict[str, Any] | None = None) -> ButtonRows:
    cover_label = "替换封面" if (data or {}).get("cover_file_id") else "上传封面"
    return [
        [("参与活动的群", "draft_group")],
        [
            ("活动标题", "draft_title"),
            (cover_label, "draft_cover"),
            ("用户限制", "draft_user_limits"),
        ],
        [
            ("准入条件", "draft_participation"),
            ("奖品内容", "draft_prizes"),
            ("抽奖说明", "draft_description"),
        ],
        [("抽奖门槛", "draft_gate")],
        [("每日发言加成", "draft_bonus_activity"), ("邀请加成", "draft_bonus_invite")],
        [("抽奖加成", "draft_rules"), ("开奖方式", "draft_draw"), ("发奖人员", "draft_issuer")],
        [("消息提示", "draft_messages"), ("🎉 发布抽奖", "draft_publish")],
        [("🗑 清空配置", "draft_clear"), ("载入上次抽奖", "draft_load_last")],
        [("↩ 返回首页", "draft_home")],
    ]


def prize_prompt_text(data: dict[str, Any]) -> str:
    return (
        "🎁 创建抽奖·设置奖品内容（必填项）\n\n"
        f"🎁 当前奖品内容：\n{_prize_lines(data.get('prizes', []))}\n\n"
        "请回复奖品名称（最多 120 字）："
    )


def prize_count_text(data: dict[str, Any]) -> str:
    return (
        "🎁 创建抽奖·设置中奖人数（必填项）\n\n"
        f"奖品：{data['pending_prize_name']}\n\n"
        "请回复正整数中奖人数："
    )


def prizes_text(data: dict[str, Any]) -> str:
    return "🎁 奖品内容\n\n" + _prize_lines(data.get("prizes", []))


def title_prompt_text(data: dict[str, Any]) -> str:
    return (
        "🏷 创建抽奖·设置标题（必填项）\n\n"
        f"当前标题：{data.get('title') or '【等待设置】'}\n\n"
        "请回复抽奖标题（最多 80 字）："
    )


def _draw_text(data: dict[str, Any]) -> str:
    if data.get("draw_mode") == "full":
        target = data.get("participant_target") or "【等待设置】"
        return f"满 {target} 人开奖（30 天兜底）"
    timezone = str(data.get("timezone") or "Asia/Shanghai")
    value = str(data.get("draw_at") or "")
    return format_public_time(value, timezone) if value else "【等待设置】"


def _user_limits(data: dict[str, Any]) -> str:
    return "用户名必填" if data.get("username_required") else "用户名可为空"


def _region_limits(data: dict[str, Any]) -> str:
    regions = data.get("allowed_regions", [])
    return "、".join(regions) if regions else "全国"


def _requirements(data: dict[str, Any]) -> str:
    chats = data.get("required_chats") or []
    if chats:
        return "必须全部满足\n" + "\n".join(
            f"  {'关注' if chat.get('type') == 'channel' else '加入'}「{chat_title(chat)}」"
            for chat in chats
        )
    count = len(data.get("required_chat_refs") or data.get("required_chat_ids", []))
    return f"{count} 项（必须全部满足）" if count else "无额外条件"


def _participation_groups(data: dict[str, Any]) -> str:
    groups = data.get("participation_groups") or []
    if not groups:
        return "【等待设置】"
    names = "、".join(chat_title(group) for group in groups)
    return f"{len(groups)} 个（必须全部加入）：{names}"


def _prize_lines(prizes: list[dict[str, Any]]) -> str:
    if not prizes:
        return "【等待设置】"
    return "\n".join(f"  💰 {item['name']} × {item['winner_count']} 人" for item in prizes)


def _base_summary(data: dict[str, Any]) -> str:
    if data.get("participation_rules_version") == "v3.0":
        return admission_summary(data)
    return base_rules_text(data) if data.get("base_activity_rules") else "基础发言门槛：未设置"
