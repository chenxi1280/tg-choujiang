from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from tg_lottery.application.admission_qualification import effective_ticket_text
from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.message_calendar_views import calendar_group_lines, calendar_window_text
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import InvitationState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import (
    AdmissionProgress,
    BaseActivityEvent,
    CampaignParticipationGroup,
    Invitation,
)


async def admission_progress_text(
    session: object,
    campaign: object,
    participant: object,
    *,
    now=None,
    group_label=None,
    compact=False,
) -> str:
    from tg_lottery.application.base_activity_views import (
        _progress_completed,
        _progress_observed_at,
    )

    rules = CampaignRules.model_validate(campaign.rules)
    timezone = ZoneInfo(rules.timezone)
    if rules.admission is None:
        return ""
    now = now or utcnow()
    observed_at = await _progress_observed_at(session, campaign, now)
    completed = _progress_completed(campaign, now)
    title = "发言数" if rules.admission.type == "message" else "邀请好友"
    status = "已完成" if participant.admission_qualified else "待完成"
    lines = [f"🚪 整场门槛: {status}" if compact else f"本场{title}门槛: {status}"]
    if observed_at is None:
        return "\n".join([*lines, "历史记录截止时间未确认, 需要核查取消或开奖记录。"])
    records = await _completion_records(session, participant.id)
    lines.extend(
        await _group_lines(
            session,
            campaign=campaign,
            participant=participant,
            rules=rules,
            observed_at=observed_at,
            records=records,
            completed=completed,
            group_label=group_label,
            compact=compact,
        )
    )
    return _progress_footer(
        lines, participant, timezone, campaign=campaign, completed=completed, compact=compact
    )


def _progress_footer(lines, participant, timezone, *, campaign, completed, compact):
    calendar = campaign.rules.get("message_task_calendar")
    if calendar:
        lines.append(calendar_window_text(campaign))
    if compact:
        return "\n".join([*lines, *(["⏹️ 本期已停止累计"] if completed else [])])
    if completed:
        lines.append("本期已停止累计; 此处为历史进度, 结果请查看参与详情。")
    else:
        active = (
            participant.admission_qualified
            and participant.qualification_state == QualificationState.ELIGIBLE
        )
        effective = effective_ticket_text(participant, active=active)
        lines.append(f"当前有效抽奖次数: {effective}")
        lines.append(
            "全部任务日分别达标后才完成整场门槛; 此前基础次数和加成暂存, 每日加成另算。"
            if calendar
            else "未完成门槛时加成暂存; 门槛本场完成一次即可, 每日加成另算。"
        )
    lines.append(_start_text(participant, timezone, start_at=campaign.preheat_at))
    return "\n".join(lines)


async def _group_lines(
    session,
    *,
    campaign,
    participant,
    rules,
    observed_at,
    records,
    completed,
    group_label,
    compact=False,
):
    gate = rules.admission
    timezone = ZoneInfo(rules.timezone)
    names = {
        str(item["id"]): item["title"]
        for item in campaign.presentation.get("participation_groups", [])
    }
    if group_label:
        names = {key: group_label(key, names.get(key, key)) for key in gate.groups}
    if gate.type == "invite":
        return await _invite_group_lines(
            session,
            participant,
            gate,
            names=names,
            records=records,
            timezone=timezone,
            compact=compact,
        )
    day = observed_at.astimezone(timezone).date()
    progress = await load_base_progress(session, campaign, participant, today=day)
    if rules.message_task_calendar:
        return await _calendar_lines(
            session, campaign, participant, progress=progress, names=names, day=day
        )
    return _message_group_lines(
        progress,
        gate,
        names=names,
        records=records,
        timezone=timezone,
        day=day,
        completed=completed,
        compact=compact,
    )


def _message_group_lines(progress, gate, *, names, records, timezone, day, completed, compact):
    if compact:
        return [
            _compact_group_line(
                names.get(key, "群名称待更新"),
                int(key) in records,
                _compact_message_progress(item, gate.groups[key]),
            )
            for key, item in progress.items()
        ]
    label = f"记录日期 {day}" if completed else "今日"
    return [
        f"{names.get(key, key)}: 本期累计 {item.today_messages}"
        f"/{gate.groups[key].daily_messages} 条" + _completed(records, key, timezone)
        if gate.groups[key].count_mode == "period"
        else f"{names.get(key, key)}: {label} {item.today_messages}"
        f"/{gate.groups[key].daily_messages} 条; 连续 {item.consecutive_days}"
        f"/{gate.groups[key].consecutive_days} 天" + _completed(records, key, timezone)
        for key, item in progress.items()
    ]


async def _invite_group_lines(session, participant, gate, *, names, records, timezone, compact):
    result = await session.execute(
        select(CampaignParticipationGroup.chat_id_snapshot, func.count(Invitation.id))
        .join(Invitation, Invitation.participation_group_id == CampaignParticipationGroup.id)
        .where(
            Invitation.inviter_participant_id == participant.id,
            Invitation.state == InvitationState.VALID,
        )
        .group_by(CampaignParticipationGroup.chat_id_snapshot)
    )
    counts = dict(result.all())
    if compact:
        return [
            _compact_group_line(
                names.get(key, "群名称待更新"),
                int(key) in records,
                f"邀请 {counts.get(int(key), 0)}/{rule.required_valid_invites} 人",
            )
            for key, rule in gate.groups.items()
        ]
    return [
        f"{names.get(key, key)}: 已确认 {counts.get(int(key), 0)}"
        f"/{rule.required_valid_invites} 人" + _completed(records, key, timezone)
        for key, rule in gate.groups.items()
    ]


def _compact_group_line(name, completed, progress):
    return f"✅ {name} · 门槛完成" if completed else f"⏳ {name} · {progress}"


def _compact_message_progress(item, rule):
    if rule.count_mode == "period":
        return f"本期累计 {item.today_messages}/{rule.daily_messages} 条"
    text = f"今日 {item.today_messages}/{rule.daily_messages} 条"
    if rule.consecutive_days > 1:
        text += f" · 连续 {item.consecutive_days}/{rule.consecutive_days} 天"
    return text


def _start_text(participant, timezone, *, start_at=None):
    if participant.counting_started_at:
        return (
            "本场统计起点: "
            + max(participant.counting_started_at, start_at or participant.counting_started_at)
            .astimezone(timezone)
            .strftime("%m-%d %H:%M:%S")
            + " (北京时间)"
        )
    return "成员等条件尚未通过, 本场行为统计尚未开始。"


async def _calendar_lines(session, campaign, participant, *, progress, names, day):
    rows = await session.execute(
        select(
            CampaignParticipationGroup.chat_id_snapshot,
            BaseActivityEvent.local_date,
            func.count(BaseActivityEvent.id),
        )
        .join(
            BaseActivityEvent,
            BaseActivityEvent.participation_group_id == CampaignParticipationGroup.id,
        )
        .where(
            BaseActivityEvent.participant_id == participant.id,
            BaseActivityEvent.accepted.is_(True),
            BaseActivityEvent.event_at >= campaign.preheat_at,
            BaseActivityEvent.event_at <= campaign.cutoff_at,
            BaseActivityEvent.local_date <= day,
        )
        .group_by(CampaignParticipationGroup.chat_id_snapshot, BaseActivityEvent.local_date)
    )
    counts = {}
    for chat, date, count in rows:
        counts.setdefault(str(chat), {})[date] = count
    rules = CampaignRules.model_validate(campaign.rules)
    lines = []
    for key, item in progress.items():
        lines.extend(
            calendar_group_lines(
                campaign,
                rules.admission.groups[key],
                item,
                name=names.get(key, "群名称待更新"),
                day=day,
                counts=counts.get(key, {}),
            )
        )
    return lines


def _completed(records: dict, key: str, timezone: object) -> str:
    completed_at = records.get(int(key))
    return f"; 本群已于 {completed_at.astimezone(timezone).date()} 完成" if completed_at else ""


async def _completion_records(session, participant_id):
    result = await session.execute(
        select(CampaignParticipationGroup.chat_id_snapshot, AdmissionProgress.completed_at)
        .join(
            AdmissionProgress,
            AdmissionProgress.participation_group_id == CampaignParticipationGroup.id,
        )
        .where(
            AdmissionProgress.participant_id == participant_id,
            AdmissionProgress.qualified.is_(True),
        )
    )
    return dict(result.all())
