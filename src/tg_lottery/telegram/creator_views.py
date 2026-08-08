from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.telegram.draft_config import (
    ACTIVITY_PRESETS,
    INVITE_PRESETS,
    format_local,
    missing_fields,
)

ButtonRows = list[list[tuple[str, str]]]


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="创建/继续抽奖", callback_data="nav:create")],
            [InlineKeyboardButton(text="我的活动", callback_data="nav:campaigns")],
            [InlineKeyboardButton(text="我的票数", callback_data="member:tickets")],
            [InlineKeyboardButton(text="邀请记录", callback_data="member:invites")],
        ]
    )


def preview_text(data: dict[str, Any]) -> str:
    missing = missing_fields(data)
    status = "可以发布" if not missing else "还需设置：" + "、".join(missing)
    return (
        "🎰 抽奖预览\n\n"
        f"👥 活动群：{data.get('group_title') or '【等待设置】'}\n"
        f"🏷 抽奖标题：{data.get('title') or '【等待设置】'}\n"
        f"🎁 奖品内容：\n{_prize_lines(data.get('prizes', []))}\n"
        f"🌀 开奖时间：{_draw_text(data)}\n"
        f"⚠️ 领奖说明：{data.get('claim_instructions') or '【等待设置】'}\n"
        f"⚖️ 中奖规则：{_rule_summary(data)}\n\n"
        f"👤 发起人：{data.get('creator_name') or '当前管理员'}\n\n"
        f"当前状态：{status}\n"
        "（本页面为抽奖卡片预览，点击下方按钮修改）"
    )


def dashboard_rows() -> ButtonRows:
    return [
        [
            ("活动群", "draft_group"),
            ("抽奖标题", "draft_title"),
            ("奖品内容", "draft_prizes"),
        ],
        [
            ("开奖时间", "draft_draw"),
            ("领奖说明", "draft_claim"),
            ("中奖规则", "draft_rules"),
        ],
        [("🎉 发布抽奖", "draft_publish")],
        [("🗑 清空配置", "draft_clear"), ("↩ 返回首页", "draft_home")],
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


def draw_prompt_text(data: dict[str, Any]) -> str:
    return (
        "🌀 创建抽奖·设置开奖时间（必填项）\n\n"
        f"当前时间：{_draw_text(data)}\n"
        "报名将在开奖前 5 分钟自动截止。\n\n"
        "请选择快捷时间，或输入自定义时间："
    )


def rules_text(data: dict[str, Any]) -> str:
    activity = ACTIVITY_PRESETS[str(data["activity_preset"])]
    invite = INVITE_PRESETS[str(data["invite_preset"])]
    return (
        "⚖️ 中奖规则\n\n"
        "成员通过群活跃和有效邀请获得抽奖票，每张票的中奖概率相同。\n\n"
        f"群活跃：每天发送 {activity.message_threshold} 条消息得 "
        f"{activity.reward_tickets} 票，每日最多 {activity.daily_ticket_cap} 票\n"
        f"有效邀请：每人奖励 {invite.reward_tickets} 票，需留存 "
        f"{invite.retention_hours} 小时\n\n"
        "开奖仍使用本项目可复算的加权随机算法。"
    )


def _draw_text(data: dict[str, Any]) -> str:
    timezone = str(data.get("timezone") or "Asia/Shanghai")
    return format_local(str(data.get("draw_at") or ""), timezone, waiting="【等待设置】")


def _rule_summary(data: dict[str, Any]) -> str:
    activity = ACTIVITY_PRESETS[str(data["activity_preset"])]
    invite = INVITE_PRESETS[str(data["invite_preset"])]
    return (
        f"活跃 {activity.message_threshold} 条得票；有效邀请留存 {invite.retention_hours} 小时得票"
    )


def _prize_lines(prizes: list[dict[str, Any]]) -> str:
    if not prizes:
        return "【等待设置】"
    return "\n".join(f"  💰 {item['name']} × {item['winner_count']} 人" for item in prizes)
