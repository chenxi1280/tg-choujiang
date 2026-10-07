from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_bonus_context import bonus_eligibility_payload
from tg_lottery.telegram.member_bonus_permissions import inspect_bonus_permissions
from tg_lottery.telegram.member_bonus_text import bonus_closed_notice, campaign_bonus_completed
from tg_lottery.telegram.member_bonus_views import build_bonus_overview
from tg_lottery.telegram.member_invites import legacy_invite_link_payload
from tg_lottery.telegram.member_window_guidance import window_guidance


def uses_legacy_bonus(rules) -> bool:
    return not (rules.admission or rules.group_invite_rules or rules.group_activity_rules)


async def bonus_read_payload(flow, campaign, rules, *, user_id):
    from tg_lottery.telegram.member_callbacks import REGISTRATION_REMINDER

    detail = await flow._queries.participant_campaign_detail(user_id, campaign.public_ref)
    if detail is None:
        raise UserFacingError(REGISTRATION_REMINDER)
    if uses_legacy_bonus(rules):
        text, keyboard = await _legacy_read(
            flow, campaign, rules=rules, detail=detail, user_id=user_id
        )
    else:
        unavailable = await inspect_bonus_permissions(flow._bot, rules)
        text, keyboard = await build_bonus_overview(
            campaign,
            user_id=user_id,
            queries=flow._queries,
            cipher=flow._cipher,
            bot_username=flow._bot_username,
            unavailable=unavailable,
        )
    return bonus_eligibility_payload(detail, text=text, keyboard=keyboard)


async def _legacy_read(flow, campaign, *, rules, detail, user_id):
    if (
        rules.invite_bonus_enabled
        and not rules.activity_bonus_enabled
        and detail.qualification is QualificationState.ELIGIBLE
    ):
        link = await flow._queries.invite_link_for_participant(campaign.id, user_id)
        if link is not None and link.encrypted_url is not None:
            return await legacy_invite_link_payload(
                campaign, user_id=user_id, queries=flow._queries, cipher=flow._cipher
            )
    return await _legacy_payload(
        flow._queries, campaign, rules=rules, detail=detail, user_id=user_id
    )


async def _legacy_payload(queries, campaign, *, rules, detail, user_id):
    lines = [
        f"{campaign.name} · 我的加成（原活动规则）",
        f"累计发言加成: {detail.activity} 次",
        f"累计邀请加成: {detail.invitation} 次",
        f"累计次数（含暂存）: {detail.total}",
    ]
    if not detail.base_qualified:
        lines.append("行为门槛尚未完成, 累计次数不代表当前有效开奖次数。")
    if rules.activity_bonus_enabled and not campaign_bonus_completed(campaign):
        progress = await queries.legacy_activity_progress(campaign.id, user_id)
        if progress is None:
            raise UserFacingError("本人发言进度暂不可读, 请刷新核查")
        lines.extend(_legacy_activity_lines(progress, rules))
    notice = bonus_closed_notice(campaign, outcome=detail.outcome)
    if notice:
        lines.append(notice)
    guidance = window_guidance(campaign, rules)
    if guidance:
        lines.append(guidance)
    rows = []
    if (
        rules.invitation_enabled
        and detail.qualification is QualificationState.ELIGIBLE
        and not campaign_bonus_completed(campaign)
    ):
        rows.append(
            [
                InlineKeyboardButton(
                    text="查看个人邀请入口", callback_data=f"member:invite:{campaign.public_ref}"
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="刷新我的加成", callback_data=f"member:bonus:{campaign.public_ref}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="返回活动详情", callback_data=f"member:tickets:{campaign.public_ref}"
                )
            ],
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _legacy_activity_lines(progress, rules):
    rule = rules.activity
    mode = "每天结算一次" if rule.settle_once_per_day else f"每日最多{rule.daily_ticket_cap}次"
    return [
        f"今日发言（{progress.local_date}）:",
        *(f"{title}: {count}/{rule.message_threshold} 条" for title, count in progress.groups),
        f"全部条件群达标后增加{rule.reward_tickets}次, {mode}",
        f"今日已入账发言加成: {progress.awarded_today} 次",
    ]
