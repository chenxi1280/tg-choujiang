import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.target_identity import duplicate_names, target_name
from tg_lottery.telegram.keyboard_layout import pair_rows, short_label
from tg_lottery.telegram.member_profile_conditions import profile_conditions
from tg_lottery.telegram.operational_issue_views import operational_issue_text
from tg_lottery.telegram.platform_condition_view import platform_condition_lines
from tg_lottery.telegram.ui_style import row_keyboard


async def condition_check_payload(
    query, *, campaign, queries, bot, profiles, cipher, relationships=None, bot_username="", page=0
):
    ref = campaign.public_ref
    targets = await queries.qualification_targets(campaign.id)
    detail = await queries.participant_campaign_detail(query.from_user.id, ref)
    issues = await queries.operational_issues(campaign.id)
    rules = CampaignRules.model_validate(campaign.rules)
    lines, rows = await target_conditions(
        bot,
        query.from_user.id,
        targets=targets,
        cipher=cipher,
        page=page,
        reference=ref,
        pending_chats=detail.pending_chat_ids if detail else (),
    )
    platform_lines, platform_rows = await platform_condition_lines(
        relationships, campaign=campaign, user_id=query.from_user.id, bot_username=bot_username
    )
    lines, rows = [*platform_lines, *lines], [*platform_rows, *rows]
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
            [
                InlineKeyboardButton(
                    text="重新核验" if detail and detail.registration_completed else "核验并报名",
                    callback_data=action,
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="返回活动详情", callback_data=f"m:detail:{ref}")])
    rows.append([InlineKeyboardButton(text="门槛与加成进度", callback_data=f"m:detail:{ref}")])
    text = f"参与条件检查 · {campaign.name}\n\n" + "\n\n".join(lines)
    return text, row_keyboard(rows)


async def target_conditions(
    bot, user_id, *, targets, cipher, page=0, reference="", pending_chats=()
):
    lines, rows = [], []
    completed = 0
    pages = max(1, (len(targets) + 5) // 6)
    page = min(max(0, page), pages - 1)
    duplicates = duplicate_names(target.title for target in targets)
    for number, target in enumerate(targets, 1):
        present, status = await _observe_target(bot, target, user_id=user_id)
        if not present and target.chat_id in pending_chats and status.startswith("❌"):
            status = "⏳ 等待管理员批准"
        completed += int(present)
        if not page * 6 < number <= (page + 1) * 6:
            continue
        title = target_name(
            target.title, position=number, chat_id=target.chat_id, duplicates=duplicates
        )
        lines.append(f"{title}：{status}")
        _target_entry(
            target,
            cipher=cipher,
            present=present,
            title=title,
            number=number,
            lines=lines,
            rows=rows,
        )
    lines.insert(
        0,
        f"入群/关注条件进度：已完成 {completed}/{len(targets)} 项（本次检查）"
        if targets
        else "无额外入群/关注条件",
    )
    rows = pair_rows(rows)
    if pages > 1:
        lines.append(f"第 {page + 1}/{pages} 页")
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"m:conditions:{reference}:{n}")
                for label, n in (("上一页", page - 1), ("下一页", page + 1))
                if 0 <= n < pages
            ]
        )
    return lines, rows


async def _observe_target(bot, target, *, user_id):
    try:
        if target.platform:
            await _require_platform_admin(bot, target)
        member = await bot.get_chat_member(target.chat_id, user_id)
        present = str(member.status) in {"creator", "administrator", "member"} or (
            str(member.status) == "restricted" and member.is_member
        )
        return present, "✅ 已加入/关注" if present else "❌ 尚未满足（已申请者请等待批准）"
    except TelegramAPIError:
        logging.getLogger(__name__).warning(
            "Membership observation failed during condition navigation",
            extra={"chat_id": target.chat_id, "user_id": user_id},
            exc_info=True,
        )
        return False, "⏳ Telegram暂时无法核验，不能视为已满足"
    except UserFacingError as exc:
        return False, f"⏳ {exc.public_message}；由平台修复，无需反复退订/重订"


def _target_entry(target, *, cipher, present, title, number, lines, rows):
    if present:
        return
    url = target_url(target, cipher)
    if url:
        rows.append([InlineKeyboardButton(text=short_label(number, title), url=url)])
    else:
        lines.append(f"{title}：目前没有已确认可用的加入入口")


async def _require_platform_admin(bot, target):
    member = await bot.get_chat_member(target.chat_id, bot.id)
    if str(member.status) not in {"administrator", "creator"}:
        raise UserFacingError("机器人管理员权限缺失，暂不能核验")


def target_url(target, cipher):
    if target.public_join_url:
        return target.public_join_url
    if target.revoked_at is not None or target.encrypted_join_url is None:
        return None
    return cipher.decrypt(target.encrypted_join_url, context=target.secret_context)
