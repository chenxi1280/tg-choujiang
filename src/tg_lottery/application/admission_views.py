from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import InvitationState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import AdmissionProgress, CampaignParticipationGroup, Invitation


async def admission_progress_text(session: object, campaign: object, participant: object) -> str:
    from tg_lottery.application.base_activity_views import (
        _progress_completed,
        _progress_observed_at,
    )

    rules = CampaignRules.model_validate(campaign.rules)
    timezone = ZoneInfo(rules.timezone)
    if rules.admission is None:
        return ""
    now = utcnow()
    observed_at = await _progress_observed_at(session, campaign, now)
    completed = _progress_completed(campaign, now)
    title = "发言数" if rules.admission.type == "message" else "邀请好友"
    status = "已完成" if participant.admission_qualified else "待完成"
    lines = [f"本场{title}门槛: {status}"]
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
        )
    )
    if completed:
        lines.append("本期已停止累计; 此处为历史进度, 结果请查看参与详情。")
    else:
        active = (
            participant.admission_qualified
            and participant.qualification_state == QualificationState.ELIGIBLE
        )
        lines.append(f"当前有效抽奖次数: {participant.ticket_total_cache if active else 0}")
        lines.append("未完成门槛时加成暂存; 门槛本场完成一次即可, 每日加成另算。")
    lines.append(_start_text(participant, timezone))
    return "\n".join(lines)


async def _group_lines(session, *, campaign, participant, rules, observed_at, records, completed):
    gate = rules.admission
    timezone = ZoneInfo(rules.timezone)
    names = {
        str(item["id"]): item["title"]
        for item in campaign.presentation.get("participation_groups", [])
    }
    if gate.type == "invite":
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
        return [
            f"{names.get(key, key)}: 已确认 {counts.get(int(key), 0)}"
            f"/{rule.required_valid_invites} 人" + _completed(records, key, timezone)
            for key, rule in gate.groups.items()
        ]
    day = observed_at.astimezone(timezone).date()
    progress = await load_base_progress(session, campaign, participant, today=day)
    label = f"记录日期 {day}" if completed else "今日"
    return [
        f"{names.get(key, key)}: {label} {item.today_messages}"
        f"/{gate.groups[key].daily_messages} 条; 连续 {item.consecutive_days}"
        f"/{gate.groups[key].consecutive_days} 天" + _completed(records, key, timezone)
        for key, item in progress.items()
    ]


def _start_text(participant, timezone):
    if participant.counting_started_at:
        return (
            "本场统计起点: "
            + participant.counting_started_at.astimezone(timezone).strftime("%m-%d %H:%M:%S")
            + " (北京时间)"
        )
    return "成员等条件尚未通过, 本场行为统计尚未开始。"


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
