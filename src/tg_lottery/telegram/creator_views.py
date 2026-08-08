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
            [InlineKeyboardButton(text="🎁 抽奖管理", callback_data="nav:index")],
            [InlineKeyboardButton(text="创建/继续抽奖", callback_data="nav:create")],
            [InlineKeyboardButton(text="我参加的抽奖", callback_data="nav:join")],
            [InlineKeyboardButton(text="设置归属地区", callback_data="nav:region")],
            [InlineKeyboardButton(text="查看我的 ID", callback_data="nav:myid")],
        ]
    )


def preview_text(data: dict[str, Any]) -> str:
    missing = missing_fields(data)
    status = "可以发布" if not missing else "还需设置：" + "、".join(missing)
    return (
        "🎰 抽奖预览\n\n"
        f"🎰 抽奖标题：{data.get('title') or '【等待设置】'}\n\n"
        f"🏞️ 封面设置：{'【已设置】' if data.get('cover_file_id') else '【等待设置】'}\n\n"
        f"🪪 用户限制：{_user_limits(data)}\n\n"
        f"🌏️ 地区限制：{_region_limits(data)}\n\n"
        f"📮 参与条件：{_participation(data)}\n\n"
        f"🎁 奖品内容：\n{_prize_lines(data.get('prizes', []))}\n"
        f"⚠️ 抽奖说明：{data.get('description') or '【等待设置】'}\n\n"
        f"🔋 人品加成：{_luck_text(data)}\n\n"
        f"🌀 开奖方式：{_draw_text(data)}\n\n"
        f"🎅 发奖人员：{data.get('issuer_name') or data.get('creator_name') or '当前管理员'}\n\n"
        f"当前状态：{status}\n"
        "（本页面为预览文案，不是正式效果）"
    )


def dashboard_rows() -> ButtonRows:
    return [
        [
            ("活动标题", "draft_title"),
            ("上传封面", "draft_cover"),
            ("用户限制", "draft_user_limits"),
        ],
        [
            ("参与条件", "draft_participation"),
            ("奖品内容", "draft_prizes"),
            ("抽奖说明", "draft_description"),
        ],
        [("人品加成", "draft_rules"), ("开奖方式", "draft_draw"), ("发奖人员", "draft_issuer")],
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


def draw_prompt_text(data: dict[str, Any]) -> str:
    return (
        "🌀 创建抽奖·设置开奖时间（必填项）\n\n"
        f"当前设置：{_draw_text(data)}\n\n"
        "请选择满人开奖或定时开奖："
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
    if data.get("draw_mode") == "full":
        target = data.get("participant_target") or "【等待设置】"
        return f"满 {target} 人开奖（30 天兜底）"
    timezone = str(data.get("timezone") or "Asia/Shanghai")
    return format_local(str(data.get("draw_at") or ""), timezone, waiting="【等待设置】")


def _rule_summary(data: dict[str, Any]) -> str:
    activity = ACTIVITY_PRESETS[str(data["activity_preset"])]
    invite = INVITE_PRESETS[str(data["invite_preset"])]
    return (
        f"活跃 {activity.message_threshold} 条得票；有效邀请留存 {invite.retention_hours} 小时得票"
    )


def _user_limits(data: dict[str, Any]) -> str:
    return "用户名必填" if data.get("username_required") else "用户名可为空"


def _region_limits(data: dict[str, Any]) -> str:
    regions = data.get("allowed_regions", [])
    return "、".join(regions) if regions else "全国"


def _participation(data: dict[str, Any]) -> str:
    group = data.get("group_title") or "【等待设置】"
    count = len(data.get("required_chat_refs") or data.get("required_chat_ids", []))
    return f"加入 {group}" + (f" 等 {count + 1} 个群/频道" if count else "")


def _luck_text(data: dict[str, Any]) -> str:
    return {"off": "关闭", "standard": "标准", "active": "高活跃"}.get(
        str(data.get("luck_mode", "standard")), "标准"
    )


def _prize_lines(prizes: list[dict[str, Any]]) -> str:
    if not prizes:
        return "【等待设置】"
    return "\n".join(f"  💰 {item['name']} × {item['winner_count']} 人" for item in prizes)
