from tg_lottery.application.records import utcnow
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.enums import CampaignState


def window_guidance(campaign, rules) -> str:
    if campaign.state in {CampaignState.DRAWN, CampaignState.CANCELLED, CampaignState.ARCHIVED}:
        return ""
    now = utcnow()
    lines = []
    if campaign.state is CampaignState.PAUSED:
        lines.append("本场人工暂停：发言停止新增计数，截止不顺延；邀请按本场原入口和核验规则处理。")
    if campaign.state is CampaignState.SCHEDULED:
        lines.append("本场尚未运行；登记、邀请入口与实际计奖起点分别按冻结规则判断。")
    if now <= campaign.cutoff_at and rules.admission is not None:
        try:
            validate_admission_window(
                rules, start_at=max(now, campaign.preheat_at), cutoff_at=campaign.cutoff_at
            )
        except ValueError as exc:
            lines.append(
                "若现在从零开始：" + str(exc) + "。已有完成或待核验事实保留，查看本人进度。"
            )
    return "\n".join(lines)
