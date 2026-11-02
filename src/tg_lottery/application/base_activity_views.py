from datetime import datetime
from zoneinfo import ZoneInfo

from tg_lottery.application.admission_qualification import effective_ticket_text
from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.campaign_outcomes import (
    TERMINAL_CAMPAIGN_STATES,
    load_campaign_outcome,
)
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.public_projection import format_public_time

BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


async def base_progress_text(
    session: object,
    campaign: object,
    participant: object,
    *,
    now: datetime | None = None,
    group_label=None,
    compact=False,
    include_timezone=True,
) -> str:
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.admission is not None:
        from tg_lottery.application.admission_views import admission_progress_text

        return await admission_progress_text(
            session,
            campaign,
            participant,
            now=now,
            group_label=group_label,
            compact=compact,
            include_timezone=include_timezone,
        )
    return await _legacy_progress_text(
        session, campaign, participant, rules=rules, now=now, group_label=group_label
    )


async def _legacy_progress_text(
    session, campaign, participant, *, rules, now=None, group_label=None
):
    if not rules.base_activity_rules:
        return ""
    now = now or utcnow()
    observed_at = await _progress_observed_at(session, campaign, now)
    if observed_at is None:
        return _unconfirmed_terminal_progress(campaign, participant, rules)
    today = observed_at.astimezone(BEIJING_TIMEZONE).date()
    completed = _progress_completed(campaign, now)
    progress = await load_base_progress(session, campaign, participant, today=today)
    groups = _group_titles(campaign)
    lines = [_qualification_text(participant.base_qualified, completed=completed)]
    date_text = format_public_time(today, rules.timezone, include_timezone=False, date_only=True)
    date_label = f"记录日期 {date_text}" if completed else "今日"
    for key, item in progress.items():
        rule = rules.base_activity_rules[key]
        name = _group_label(key, groups.get(key) or "名称待更新", group_label)
        status = (
            "本群已达标"
            if item.qualified
            else f"连续 {item.consecutive_days}/{rule.consecutive_days} 天"
        )
        lines.append(
            f"{name}\uff1a {date_label} {item.today_messages}/{rule.daily_messages} 条\uff1b"
            f" {status}"
        )
    if completed:
        lines.append("本期基础发言已停止累计\uff1b 此处为历史进度\uff0c 开奖结果请查看参与详情。")
        return "\n".join(lines)
    active = (
        participant.base_qualified
        and participant.qualification_state == QualificationState.ELIGIBLE
    )
    effective = effective_ticket_text(participant, active=active)
    lines.append(f"当前有效抽奖次数\uff1a {effective}")
    lines.append(
        "已获得的加成将在门槛和参与条件满足后生效\uff1b 缺勤会中断连续进度\uff1b "
        + "本群门槛无需重做\uff0c参与开奖仍须满足全部参与条件。"
    )
    return "\n".join(lines)


def _group_label(key, name, formatter):
    return formatter(key, name) if formatter else name


def _qualification_text(qualified: bool, *, completed: bool) -> str:
    if qualified:
        return "基础发言门槛\uff1a 已达标"
    suffix = "本期已停止累计" if completed else "尚未生效"
    return f"基础发言门槛\uff1a 未达标\uff0c {suffix}"


async def _progress_observed_at(
    session: object, campaign: object, now: datetime
) -> datetime | None:
    if campaign.state not in TERMINAL_CAMPAIGN_STATES:
        return min(now, campaign.cutoff_at)
    outcome = await load_campaign_outcome(session, campaign.id)
    if outcome.kind == "cancelled":
        return min(outcome.cancelled_at, campaign.cutoff_at)
    if outcome.kind == "drawn":
        return campaign.cutoff_at
    return None


def _unconfirmed_terminal_progress(
    campaign: object, participant: object, rules: CampaignRules
) -> str:
    groups = _group_titles(campaign)
    lines = [_qualification_text(participant.base_qualified, completed=True)]
    lines.extend(
        f"{groups.get(key) or '名称待更新'}\uff1a 记录日期未确认\uff0c 历史条数和连续进度未确认"
        for key in rules.base_activity_rules
    )
    lines.append("本期基础发言已停止累计\uff1b 此处为历史进度。")
    lines.append(
        "无法确认记录截止日期\uff1a 活动缺少取消时间审计记录及已完成的开奖记录\uff0c 需要核查。"
    )
    return "\n".join(lines)


def _group_titles(campaign: object) -> dict[str, str]:
    return {
        str(group["id"]): group["title"]
        for group in campaign.presentation.get("participation_groups", [])
    }


def _progress_completed(campaign: object, now: datetime) -> bool:
    return now > campaign.cutoff_at or campaign.state not in {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
        CampaignState.PAUSED,
    }
