from datetime import datetime
from zoneinfo import ZoneInfo

from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.campaign_outcomes import (
    TERMINAL_CAMPAIGN_STATES,
    load_campaign_outcome,
)
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules

BEIJING_TIMEZONE = ZoneInfo("Asia/Shanghai")


async def base_progress_text(session: object, campaign: object, participant: object) -> str:
    rules = CampaignRules.model_validate(campaign.rules)
    if rules.admission is not None:
        from tg_lottery.application.admission_views import admission_progress_text

        return await admission_progress_text(session, campaign, participant)
    return await _legacy_progress_text(session, campaign, participant, rules=rules)


async def _legacy_progress_text(session, campaign, participant, *, rules):
    if not rules.base_activity_rules:
        return ""
    now = utcnow()
    observed_at = await _progress_observed_at(session, campaign, now)
    if observed_at is None:
        return _unconfirmed_terminal_progress(campaign, participant, rules)
    today = observed_at.astimezone(BEIJING_TIMEZONE).date()
    completed = _progress_completed(campaign, now)
    progress = await load_base_progress(session, campaign, participant, today=today)
    groups = _group_titles(campaign)
    lines = [_qualification_text(participant.base_qualified, completed=completed)]
    date_label = f"记录日期 {today}" if completed else "今日"
    for key, item in progress.items():
        rule = rules.base_activity_rules[key]
        status = (
            "本群已达标"
            if item.qualified
            else f"连续 {item.consecutive_days}/{rule.consecutive_days} 天"
        )
        lines.append(
            f"{groups.get(key) or '名称待更新'}: {date_label} "
            f"{item.today_messages}/{rule.daily_messages} 条; {status}"
        )
    if completed:
        lines.append("本期基础发言已停止累计; 此处为历史进度, 开奖结果请查看参与详情。")
        return "\n".join(lines)
    active = (
        participant.base_qualified
        and participant.qualification_state == QualificationState.ELIGIBLE
    )
    lines.append(f"当前有效抽奖次数: {participant.ticket_total_cache if active else 0}")
    lines.append("未达标时加成暂存; 缺勤会中断连续进度; 完成一次后保留本期达标资格。")
    return "\n".join(lines)


def _qualification_text(qualified: bool, *, completed: bool) -> str:
    if qualified:
        return "基础发言门槛: 已达标"
    suffix = "本期已停止累计" if completed else "加成暂存"
    return f"基础发言门槛: 未达标, {suffix}"


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
        f"{groups.get(key) or '名称待更新'}: 记录日期未确认, 历史条数和连续进度未确认"
        for key in rules.base_activity_rules
    )
    lines.append("本期基础发言已停止累计; 此处为历史进度。")
    lines.append("无法确认记录截止日期: 活动缺少取消时间审计记录及已完成的开奖记录, 需要核查。")
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
