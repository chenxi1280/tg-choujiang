from __future__ import annotations

from datetime import datetime
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.bonus_rule_text import chat_title
from tg_lottery.telegram.creator_base_activity import base_rules_text
from tg_lottery.telegram.creator_behavior_views import admission_summary
from tg_lottery.telegram.creator_bonus import bonus_summary
from tg_lottery.telegram.creator_bonus_warnings import bonus_window_warning
from tg_lottery.telegram.creator_invite_retention import summary as retention_summary
from tg_lottery.telegram.creator_messages import message_policy_summary
from tg_lottery.telegram.creator_sections import next_draft_action
from tg_lottery.telegram.draft_config import campaign_input, missing_fields
from tg_lottery.telegram.home_views import home_keyboard
from tg_lottery.telegram.platform_requirements import platform_summary
from tg_lottery.telegram.public_projection import format_public_time

ButtonRows = list[list[tuple[str, str]]]


def main_keyboard() -> InlineKeyboardMarkup:
    return home_keyboard()


def preview_text(data: dict[str, Any], *, now: datetime | None = None) -> str:
    status = _preview_status(data, now or utcnow())
    return (
        "📝 创建抽奖 · 编辑草稿（未发布）\n"
        f"下一项：{next_draft_action(data)[0]}\n\n"
        f"🏷️ 抽奖标题：{data.get('title') or '【等待设置】'}\n"
        f"🖼️ 封面设置：{'【已设置】' if data.get('cover_file_id') else '【等待设置】'}\n"
        f"🪪 用户限制：{_user_limits(data)}\n"
        f"🌐 地区限制：{_region_limits(data)}\n\n"
        f"👥 参与活动的群：{_participation_groups(data)}\n"
        f"🧭 准入条件：{_requirements(data)}\n\n"
        f"{platform_summary(data)}"
        f"{_base_summary(data)}\n\n"
        f"🎁 奖品内容：\n{_prize_lines(data.get('prizes', []))}\n"
        f"📃 抽奖说明：{data.get('description') or '【等待设置】'}\n\n"
        f"📈 抽奖加成：{bonus_summary(data)}\n{retention_summary(data)}\n"
        f"⏰ 开奖时间：{_draw_text(data)}\n"
        f"🧑‍💼 发奖人员：{data.get('issuer_name') or data.get('creator_name') or '当前管理员'}\n"
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
    return [
        [("活动标题", "draft_title"), ("奖品与领奖", "draft_prizes")],
        [("参与群", "draft_group"), ("开奖时间", "draft_draw")],
        [("参与条件", "draft_conditions"), ("加成设置", "draft_rules")],
        [("封面与说明", "draft_cover_summary"), ("更多设置", "draft_more")],
        [next_draft_action(data or {})],
        [("返回首页（草稿保留）", "draft_home")],
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
        "🏷️ 创建抽奖·设置标题（必填项）\n\n"
        f"当前标题：{data.get('title') or '【等待设置】'}\n"
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
    return "\n".join(f"  📦 {item['name']} × {item['winner_count']} 人" for item in prizes)


def _base_summary(data: dict[str, Any]) -> str:
    if data.get("participation_rules_version") == "v3.0":
        return admission_summary(data)
    return base_rules_text(data) if data.get("base_activity_rules") else "基础发言门槛：未设置"
