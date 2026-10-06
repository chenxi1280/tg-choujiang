import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.target_identity import duplicate_names, target_name
from tg_lottery.telegram.member_profile_conditions import profile_conditions
from tg_lottery.telegram.operational_issue_views import operational_issue_text


async def condition_check_payload(query, *, campaign, queries, bot, profiles, cipher):
    ref = campaign.public_ref
    targets = await queries.qualification_targets(campaign.id)
    detail = await queries.participant_campaign_detail(query.from_user.id, ref)
    issues = await queries.operational_issues(campaign.id)
    rules = CampaignRules.model_validate(campaign.rules)
    lines, rows = await target_conditions(bot, query.from_user.id, targets=targets, cipher=cipher)
    profile_lines, profile_rows = await profile_conditions(
        profiles, query, rules=rules, reference=ref
    )
    lines, rows = [*lines, *profile_lines], [*rows, *profile_rows]
    if issues:
        lines.append(operational_issue_text(issues))
    if (
        campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED}
        and utcnow() <= campaign.cutoff_at
        and (detail is not None or not issues)
    ):
        action = f"member:verify:{ref}" if detail else f"member:join:{ref}"
        rows.append(
            [InlineKeyboardButton(text="重新核验" if detail else "登记参与", callback_data=action)]
        )
    rows.append([InlineKeyboardButton(text="返回活动详情", callback_data=f"m:detail:{ref}")])
    text = f"参与条件检查 · {campaign.name}\n\n" + "\n\n".join(lines)
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def target_conditions(bot, user_id, *, targets, cipher):
    lines, rows = [], []
    duplicates = duplicate_names(target.title for target in targets)
    for number, target in enumerate(targets, 1):
        title = target_name(
            target.title, position=number, chat_id=target.chat_id, duplicates=duplicates
        )
        try:
            member = await bot.get_chat_member(target.chat_id, user_id)
            present = str(member.status) in {"creator", "administrator", "member"} or (
                str(member.status) == "restricted" and member.is_member
            )
            status = "已加入/关注" if present else "尚未满足（已申请者请等待批准）"
            lines.append(f"{title}：{status}")
        except TelegramAPIError:
            logging.getLogger(__name__).warning(
                "Membership observation failed during condition navigation",
                extra={"chat_id": target.chat_id, "user_id": user_id},
                exc_info=True,
            )
            lines.append(f"{title}：Telegram暂时无法核验，不能视为已满足")
        url = target_url(target, cipher)
        if url:
            rows.append([InlineKeyboardButton(text="打开「" + title + "」", url=url)])
        else:
            lines.append(f"{title}：目前没有已确认可用的加入入口")
    return lines, rows


def target_url(target, cipher):
    if target.public_join_url:
        return target.public_join_url
    if target.revoked_at is not None or target.encrypted_join_url is None:
        return None
    return cipher.decrypt(target.encrypted_join_url, context=target.secret_context)
