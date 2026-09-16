from zoneinfo import ZoneInfo

from tg_lottery.application.base_activity import load_base_progress
from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules


async def base_progress_text(session: object, campaign: object, participant: object) -> str:
    rules = CampaignRules.model_validate(campaign.rules)
    if not rules.base_activity_rules:
        return ""
    today = min(utcnow(), campaign.cutoff_at).astimezone(ZoneInfo("Asia/Shanghai")).date()
    progress = await load_base_progress(session, campaign, participant, today=today)
    groups = {
        str(group["id"]): group["title"]
        for group in campaign.presentation.get("participation_groups", [])
    }
    lines = [
        "基础发言门槛: 已达标" if participant.base_qualified else "基础发言门槛: 未达标, 加成暂存"
    ]
    for key, item in progress.items():
        rule = rules.base_activity_rules[key]
        status = (
            "本群已达标"
            if item.qualified
            else f"连续 {item.consecutive_days}/{rule.consecutive_days} 天"
        )
        lines.append(
            f"{groups.get(key, key)}: 今日 {item.today_messages}/{rule.daily_messages} 条; {status}"
        )
    active = (
        participant.base_qualified
        and participant.qualification_state == QualificationState.ELIGIBLE
    )
    lines.append(f"当前有效抽奖次数: {participant.ticket_total_cache if active else 0}")
    lines.append("未达标时加成暂存; 缺勤会中断连续进度; 完成一次后保留本期达标资格。")
    return "\n".join(lines)
