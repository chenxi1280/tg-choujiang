from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.member_bonus_text import activity_cap, bonus_closed_notice, bonus_header
from tg_lottery.telegram.member_group_invites import (
    _activity_progress_line,
    campaign_bonus_completed,
)


def required_group(context, chat_id: int) -> dict:
    group = next((item for item in context.groups if int(item["id"]) == chat_id), None)
    if group is None:
        raise ValueError("参与群不存在或已移除")
    return group


def task_back(context, category: str, *, page: int):
    groups = [g for g in context.groups if _enabled(context, category, int(g["id"]))]
    ref = context.campaign.public_ref
    if len(groups) == 1:
        return InlineKeyboardButton(text="返回活动详情", callback_data=f"member:tickets:{ref}")
    prefix = "bonus_invites" if category == "invites" else "bonus_activity"
    label = "返回邀请群列表" if category == "invites" else "返回活跃群列表"
    return InlineKeyboardButton(text=label, callback_data=f"member:{prefix}:{ref}:{page}")


def _enabled(context, category: str, chat_id: int) -> bool:
    if category == "invites":
        return context.rules.invite_target_enabled(chat_id)
    return context.rules.activity_rule_for(chat_id) is not None


def invite_detail_navigation(context, chat_id: int, *, return_page: int) -> list:
    ref = context.campaign.public_ref
    return [
        [
            InlineKeyboardButton(
                text="邀请记录", callback_data=f"m:ir:{ref}:{chat_id}:{return_page}:all:0"
            )
        ],
        [
            InlineKeyboardButton(
                text="刷新本群状态", callback_data=f"m:id:{ref}:{chat_id}:{return_page}"
            )
        ],
        [task_back(context, "invites", page=return_page)],
    ]


def activity_group_detail(context, chat_id: int, *, return_page: int):
    group = required_group(context, chat_id)
    rule = context.rules.activity_rule_for(chat_id)
    if rule is None:
        raise ValueError("该群未开启活跃加成（人品）")
    progress = context.activity_progress.get(chat_id)
    closed = campaign_bonus_completed(context.campaign)
    lines = [
        bonus_header(
            context.campaign, f"{group['title']} 活跃加成（人品）", outcome=context.outcome
        ),
        f"规则：每{rule.messages_per_cycle}条有效消息增加{rule.reward_tickets}次抽奖机会",
        "文字、图片、表情均计数；" + activity_cap(rule),
    ]
    if progress is None:
        lines.append("当前发言进度暂未确认，请刷新核查，不能视为0条或已完成。")
    else:
        lines.append(_activity_progress_line(str(group["title"]), rule, progress, completed=closed))
    lines.extend(
        [
            bonus_closed_notice(context.campaign, category="活跃", outcome=context.outcome),
            context.base_progress,
        ]
    )
    if chat_id in context.unavailable:
        lines.append(context.unavailable[chat_id])
    rows = _activity_rows(context, group, chat_id=chat_id, return_page=return_page, closed=closed)
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _activity_rows(context, group, *, chat_id: int, return_page: int, closed: bool):
    ref = context.campaign.public_ref
    rows = []
    active = (
        context.campaign.state is CampaignState.RUNNING and utcnow() >= context.campaign.preheat_at
    )
    if group.get("join_url") and not closed and active:
        rows.append([InlineKeyboardButton(text="去本群发言", url=str(group["join_url"]))])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="刷新活跃进度", callback_data=f"m:ad:{ref}:{chat_id}:{return_page}"
                )
            ],
            [task_back(context, "activity", page=return_page)],
        ]
    )
    return rows
