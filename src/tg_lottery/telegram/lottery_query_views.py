import html

from aiogram.types import InlineKeyboardButton

from tg_lottery.application.invitation_models import campaign_accepts_attribution
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.member_bonus_text import invite_bonus_completed
from tg_lottery.telegram.member_record_text import current_effective_count
from tg_lottery.telegram.panel_pagination import text_pages
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
    format_public_time,
    qualification_state_text,
)


def activity_url(username, reference, *, action="join"):
    return f"https://t.me/{username}?start={action}_{reference}"


def discovery(campaigns, *, username, page):
    pages = max(1, (len(campaigns) + 4) // 5)
    page = min(max(page, 0), pages - 1)
    lines = ["🎁 本群进行中的抽奖"]
    for campaign in campaigns[page * 5 : (page + 1) * 5]:
        url = html.escape(activity_url(username, campaign.public_ref), quote=True)
        name = html.escape(campaign.name)
        state = campaign_state_text(campaign.state)
        if campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED}:
            state = _open_label(campaign)
        lines.append(
            f'🎟️ <a href="{url}">{name}</a>\n└ {state}\n'
            f"截止 {format_public_time(campaign.cutoff_at, campaign.timezone, seconds=True)}\n"
            f"开奖 {format_public_time(campaign.draw_at, campaign.timezone, seconds=True)}"
        )
    if not campaigns:
        lines.append("📭 本群当前没有可报名的抽奖。")
    lines.append(f"第 {page + 1}/{pages} 页 · 点击活动名进入机器人查看并报名")
    return "\n\n".join(lines), page, pages


async def personal_progress(queries, campaign, *, user_id):
    detail = await queries.participant_campaign_detail(user_id, campaign.public_ref)
    if detail is None:
        return ("📊 抽奖进度\n暂无本场报名记录；查看不会自动报名。",)
    rules = CampaignRules.model_validate(campaign.rules)
    status = "✅ 已报名" if detail.registration_completed else "⏳ 报名待完成"
    blocks = [
        f"🎁 {campaign.name} · {status}\n活动编号：{campaign.public_ref}",
        f"📌 {campaign_state_text(campaign.state)}；仅在活动运行且原规则允许的时段累计。",
        "🚪 门槛进度",
        "🛂 参与资格：" + qualification_state_text(detail.qualification),
        detail.membership_progress or "无额外入群/关注条件",
        detail.base_progress or "无额外行为门槛",
        "📈 加成进度\n" + _activity_status(campaign),
        f"🌱 发言抽奖加成：累计 {detail.activity} 次"
        if rules.activity_bonus_enabled
        else "🌱 发言抽奖加成：未开启",
        f"🤝 邀请抽奖加成：累计 {detail.invitation} 次"
        if rules.invite_bonus_enabled
        else "🤝 邀请抽奖加成：未开启",
    ]
    blocks.extend(await _daily_bonus(queries, campaign, user_id=user_id, rules=rules))
    blocks.extend(
        [
            f"🎟️ 基础 {detail.registration} 次 · 有效 {current_effective_count(detail)} 次",
            _settlement_note(campaign),
            "🤝 邀请进度",
        ]
    )
    blocks.extend(await _invitations(queries, campaign, user_id=user_id, rules=rules))
    text = "\n".join(filter(None, blocks))
    pages = text_pages(text, limit=3000)
    if len(pages) == 1:
        return pages
    records = await queries.campaign_invitation_records(user_id, campaign.public_ref)
    invitation_summary = (
        f"有效 {records.valid} 人 · 待核验 {records.pending} 人 · 无效 {records.invalid} 人"
        if records
        else "本场未配置邀请任务"
    )
    gate_state = "✅ 行为门槛已完成" if detail.base_qualified else "❌ 行为门槛未全部完成"
    overview = (
        f"🎁 {campaign.name} · {status}\n"
        f"🚪 门槛进度：{gate_state}\n"
        f"参与资格：{qualification_state_text(detail.qualification)}（逐群明细见后续页）\n"
        f"📈 加成进度：发言累计 {detail.activity} 次 · 邀请累计 {detail.invitation} 次\n"
        f"🎟️ 基础 {detail.registration} 次 · 当前有效 {current_effective_count(detail)} 次\n"
        f"🤝 邀请进度：{invitation_summary}\n门槛、加成及邀请逐群明细请翻页。"
    )
    return (overview, *pages)


async def _daily_bonus(queries, campaign, *, user_id, rules):
    if not rules.activity_bonus_enabled:
        return []
    if not rules.group_activity_rules:
        return await _legacy_activity(queries, campaign, user_id=user_id, rules=rules)
    progress = await queries.group_bonus_progress(campaign.id, user_id)
    if progress is None:
        raise ValueError("发言加成进度暂时无法获取")
    names = _group_names(campaign)
    lines = []
    for row in progress.activities:
        rule = rules.activity_rule_for(row.chat_id)
        if rule is None:
            continue
        lines.append(f"{names.get(row.chat_id, '活动群')}：" + _activity_line(campaign, rule, row))
    return lines


async def _legacy_activity(queries, campaign, *, user_id, rules):
    if _tasks_closed(campaign):
        detail = await queries.participant_campaign_detail(user_id, campaign.public_ref)
        if detail is None:
            raise ValueError("历史发言加成暂时无法获取")
        return [f"发言抽奖加成累计 {detail.activity} 次（按本场原规则入账）"]
    legacy = await queries.legacy_activity_progress(campaign.id, user_id)
    if legacy is None:
        raise ValueError("历史发言进度暂时无法获取")
    names = _group_names(campaign)
    return [
        f"{names.get(chat, '活动群')}：今日 {count}/{rules.activity.message_threshold} 条"
        for chat, count in legacy.group_counts
    ]


async def _invitations(queries, campaign, *, user_id, rules):
    if not rules.invitation_enabled:
        return ["本场未配置邀请门槛或邀请奖励。"]
    targets = await queries.qualification_targets(campaign.id)
    names = _group_names(campaign)
    rows = [_invitation_status(campaign, rules)]
    grouped = False
    for target in targets:
        if target.kind != "participation_group" or not rules.invite_target_enabled(target.chat_id):
            continue
        records = await queries.campaign_invitation_records(
            user_id, campaign.public_ref, chat_id=target.chat_id
        )
        if records is None:
            raise ValueError("邀请进度暂时无法获取")
        gate = (
            rules.admission.groups.get(str(target.chat_id))
            if rules.invite_admission_enabled
            else None
        )
        suffix = _invite_target(campaign, rules, gate, valid=records.valid)
        grouped = True
        rows.append(
            f"{names.get(target.chat_id, target.title)}：有效 {records.valid} 人 · "
            f"待核验 {records.pending} 人 · "
            f"无效 {records.invalid} 人{suffix}"
        )
    if not grouped:
        records = await queries.campaign_invitation_records(user_id, campaign.public_ref)
        if records is None:
            raise ValueError("邀请进度暂时无法获取")
        rows.append(
            f"有效 {records.valid} 人 · 待核验 {records.pending} 人 · 无效 {records.invalid} 人"
        )
    return rows


def page_buttons(key, *, page, pages, section=0, sections=1):
    rows = []
    if sections > 1:
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"lq:{key}:{page}:{part}")
                for label, part in (("上一段", section - 1), ("下一段", section + 1))
                if 0 <= part < sections
            ]
        )
    if pages > 1:
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"lq:{key}:{number}:0")
                for label, number in (("上一页", page - 1), ("下一页", page + 1))
                if 0 <= number < pages
            ]
        )
    return rows


def _open_label(campaign):
    if utcnow() >= campaign.cutoff_at:
        return "报名已截止 · 待开奖"
    return (
        "尚未开始 · 可先报名，开始后才计数"
        if campaign.state == CampaignState.SCHEDULED
        else "报名中"
    )


def _group_names(campaign):
    from collections import Counter

    groups = campaign.presentation.get("participation_groups", [])
    counts = Counter(row["title"] for row in groups)
    return {
        int(row["id"]): (
            f"{row['title']}（{row['id']}）" if counts[row["title"]] > 1 else row["title"]
        )
        for row in groups
    }


async def current_task_summary(queries, campaign, *, user_id):
    rules = CampaignRules.model_validate(campaign.rules)
    daily = await _daily_bonus(queries, campaign, user_id=user_id, rules=rules)
    invitation = await _invitations(queries, campaign, user_id=user_id, rules=rules)
    empty = (
        "未配置可用的发言加成群。" if rules.activity_bonus_enabled else "本场未开启发言抽奖加成。"
    )
    title = "🌱 发言抽奖加成累计记录" if _tasks_closed(campaign) else "🌱 今日发言抽奖加成"
    lines = [title, _activity_status(campaign), *(daily or [empty])]
    lines.extend(["🤝 邀请进度", *invitation])
    return "\n".join(lines)


def _tasks_closed(campaign):
    return (
        campaign.state not in {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED}
        or utcnow() >= campaign.cutoff_at
    )


def _activity_status(campaign):
    if _tasks_closed(campaign):
        return "本场已停止新增任务，以下保留累计记录；截止前事实仍按原规则处理。"
    if campaign.state == CampaignState.PAUSED:
        return "⏸️ 活动暂停，发言停止新增计数，已有进度保留，截止不顺延。"
    if campaign.state == CampaignState.SCHEDULED or utcnow() < campaign.preheat_at:
        return "⏰ 活动尚未运行，发言计数尚未开始。"
    return "仅在前置资格通过且本场允许的时段内累计发言。"


def _settlement_note(campaign):
    if _tasks_closed(campaign):
        return "累计记录按原账本保留；最终开奖次数以已保存的开奖快照为准。"
    return "门槛或资格未通过时加成暂存；开奖仍须最终核验。"


def _activity_line(campaign, rule, progress):
    if _tasks_closed(campaign):
        return (
            f"累计达标 {progress.qualified_days} 天 · "
            f"发言加成累计 {progress.total_awarded_tickets} 次"
        )
    recorded = f"今日 {progress.valid_messages} 条 · 今日 +{progress.awarded_tickets} 次"
    if campaign.state != CampaignState.RUNNING or utcnow() < campaign.preheat_at:
        return recorded
    from tg_lottery.application.activity_bonus_reminders import bonus_cycle_text

    return recorded + " · " + bonus_cycle_text(rule, progress.valid_messages)


def _invitation_open(campaign, rules):
    return (
        not _tasks_closed(campaign)
        and campaign_accepts_attribution(campaign, utcnow())
        and not invite_bonus_completed(campaign, retention_hours=rules.invite.retention_hours)
    )


def _invitation_status(campaign, rules):
    if _tasks_closed(campaign):
        return "不再引导新增邀请；已记录邀请及待核验事实按本场原规则处理。"
    if invite_bonus_completed(campaign, retention_hours=rules.invite.retention_hours):
        return "最晚计奖入群时间已过；仅核验此前邀请，保留累计记录。"
    if not campaign_accepts_attribution(campaign, utcnow()):
        return "邀请计奖窗口尚未开始，已有记录保留。"
    return "邀请按本场原有效入群窗口与留存规则核验。"


def _invite_target(campaign, rules, gate, *, valid):
    if gate is None:
        return ""
    text = f" · 本场门槛目标 {gate.required_valid_invites} 人"
    if _invitation_open(campaign, rules):
        text += f"，还差 {max(0, gate.required_valid_invites - valid)} 人"
    return text
