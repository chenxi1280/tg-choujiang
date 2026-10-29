from datetime import timedelta

from tg_lottery.application.campaign_outcomes import CampaignOutcomeFacts
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
HISTORICAL_CAMPAIGN_STATES = frozenset(
    {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}
)


def campaign_bonus_completed(campaign: object) -> bool:
    return campaign.state in BONUS_CLOSED_STATES or utcnow() > campaign.cutoff_at


def invite_bonus_completed(campaign: object, *, retention_hours: int) -> bool:
    deadline = campaign.cutoff_at - timedelta(hours=retention_hours)
    return campaign_bonus_completed(campaign) or utcnow() > deadline


def historical_outcome_status(
    campaign: object, *, outcome: CampaignOutcomeFacts | None
) -> str | None:
    if campaign.state not in HISTORICAL_CAMPAIGN_STATES:
        return None
    if outcome is not None and outcome.kind == "drawn":
        return "本场已开奖"
    if outcome is not None and outcome.kind == "cancelled":
        return "本场已取消，未开奖"
    return "本场开奖结果尚未确认，请联系创建者核查"


def bonus_closed_notice(
    campaign: object, *, category: str = "", outcome: CampaignOutcomeFacts | None = None
) -> str:
    if not campaign_bonus_completed(campaign):
        return ""
    status = historical_outcome_status(campaign, outcome=outcome) or "活动已截止"
    category = {"邀请": "邀请抽奖", "发言": "发言抽奖", "活跃": "发言抽奖"}.get(category, category)
    return f"{status}，不再计算新增{category}加成；以下保留本期累计记录。"


def invite_list_prompt(
    campaign: object, *, retention_hours: int, outcome: CampaignOutcomeFacts | None = None
) -> str:
    status = historical_outcome_status(campaign, outcome=outcome)
    if status is not None:
        return f"{status}，邀请入口已隐藏；可查看历史邀请记录，仍有效的旧链接可入群。"
    if campaign_bonus_completed(campaign):
        return bonus_closed_notice(campaign, category="邀请", outcome=outcome)
    if invite_bonus_completed(campaign, retention_hours=retention_hours):
        return "最晚计奖入群时间已过；仅核验此前邀请，可查看本期记录。"
    return "请选择一个群查看本期链接和邀请记录："


def bonus_header(
    campaign: object, title: str, *, outcome: CampaignOutcomeFacts | None = None
) -> str:
    moment = campaign.draw_at
    label = "开奖"
    if campaign.state in HISTORICAL_CAMPAIGN_STATES:
        label = "计划开奖"
        if outcome is not None and outcome.kind == "drawn" and outcome.completed_at is not None:
            label = "实际开奖"
            moment = outcome.completed_at
    return (
        f"{'🤝' if '邀请' in title else '🌱'} {title}\n活动：{campaign.name}\n"
        f"{label}：{format_public_time(moment, campaign.timezone)}"
    )


def invite_cap(rule: object) -> str:
    return "不设上限" if rule.cap_mode == "unlimited" else f"上限 {rule.max_bonus_tickets} 次"


def invitation_section_title(rules: object) -> str:
    if rules.admission and rules.admission.type == "invite":
        return "邀请门槛与加成" if rules.invite_bonus_enabled else "邀请门槛与入口"
    return "邀请抽奖加成"


def invitation_category_label(rules: object, total: int) -> str:
    title = "🤝 " + invitation_section_title(rules)
    return f"{title}｜{total} 次" if rules.invite_bonus_enabled else title


def activity_cap(rule: object) -> str:
    return (
        "每日不设上限"
        if rule.daily_cap_mode == "unlimited"
        else f"每日上限 {rule.daily_max_cycles} 次"
    )
