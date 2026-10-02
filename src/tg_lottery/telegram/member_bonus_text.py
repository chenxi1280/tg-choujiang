from datetime import timedelta

from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.public_projection import format_public_time

BONUS_CLOSED_STATES = frozenset(
    {
        CampaignState.READY_TO_DRAW,
        CampaignState.DRAWING,
        CampaignState.DRAWN,
        CampaignState.ARCHIVED,
        CampaignState.CANCELLED,
    }
)


def campaign_bonus_completed(campaign: object) -> bool:
    return campaign.state in BONUS_CLOSED_STATES or utcnow() > campaign.cutoff_at


def invite_bonus_completed(campaign: object, *, retention_hours: int) -> bool:
    deadline = campaign.cutoff_at - timedelta(hours=retention_hours)
    return campaign_bonus_completed(campaign) or utcnow() > deadline


def bonus_closed_notice(campaign: object, *, category: str = "") -> str:
    if not campaign_bonus_completed(campaign):
        return ""
    if campaign.state is CampaignState.CANCELLED:
        status = "本场已取消"
    elif campaign.state in {CampaignState.DRAWN, CampaignState.ARCHIVED}:
        status = "本场已开奖"
    else:
        status = "活动已截止"
    return f"{status}，不再计算新增{category}加成；以下保留本期累计记录。"


def invite_list_prompt(campaign: object, *, retention_hours: int) -> str:
    if campaign.state in {CampaignState.DRAWN, CampaignState.ARCHIVED}:
        return "本场已开奖，邀请入口已隐藏；可查看历史邀请记录，仍有效的旧链接可入群。"
    if campaign.state is CampaignState.CANCELLED:
        return "本场已取消，邀请入口已隐藏；可查看历史邀请记录。"
    if campaign_bonus_completed(campaign):
        return bonus_closed_notice(campaign, category="邀请")
    if invite_bonus_completed(campaign, retention_hours=retention_hours):
        return "最晚计奖入群时间已过；仅核验此前邀请，可查看本期记录。"
    return "请选择一个群查看本期链接和邀请记录："


def bonus_header(campaign: object, title: str) -> str:
    return (
        f"🔋 {title}\n活动：{campaign.name}\n"
        f"开奖：{format_public_time(campaign.draw_at, campaign.timezone)}"
    )


def invite_cap(rule: object) -> str:
    return "不设上限" if rule.cap_mode == "unlimited" else f"上限 {rule.max_bonus_tickets} 次"


def activity_cap(rule: object) -> str:
    return (
        "每日不设上限"
        if rule.daily_cap_mode == "unlimited"
        else f"每日上限 {rule.daily_max_cycles} 次"
    )
