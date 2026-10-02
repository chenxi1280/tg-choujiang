from zoneinfo import ZoneInfo

from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.rules import CampaignRules


async def base_progress_text(session: object, campaign: object, participant: object) -> str:
    rules = CampaignRules.model_validate(campaign.rules)
    if not rules.base_activity_rules:
        return ""
    now = utcnow()
    today = min(now, campaign.cutoff_at).astimezone(ZoneInfo("Asia/Shanghai")).date()
    completed = _progress_completed(campaign, now)
    progress = await load_base_progress(session, campaign, participant, today=today)
    groups = {
        str(group["id"]): group["title"]
        for group in campaign.presentation.get("participation_groups", [])
    }
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


def _progress_completed(campaign: object, now: object) -> bool:
    return now > campaign.cutoff_at or campaign.state not in {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
        CampaignState.PAUSED,
    }
