from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.public_projection import campaign_state_text, format_public_time

MANAGEMENT_ACTION_LABELS = {
    "view_winners": "查看中奖名单/登记已领奖",
    "pause": "暂停",
    "resume": "恢复",
    "early_cutoff": "提前截止",
    "draw": "立即开奖",
    "cancel": "取消活动",
    "refresh_publication": "🔄 刷新发布状态",
    "publication_issues": "查看发布异常",
    "retry_publication": "重试明确失败项",
    "retry_qualification": "重新复核资格",
}


def management_text(campaign, *, stats, deliveries, details):
    text = (
        f"{campaign.name}\n状态: {campaign_state_text(campaign.state)}\n"
        f"规则版本：{campaign.config_revision}\n"
        f"截止：{format_public_time(campaign.cutoff_at, campaign.timezone)}\n"
        f"计划开奖：{format_public_time(campaign.draw_at, campaign.timezone)}\n"
        f"报名: {stats.participants}\n有效邀请: {stats.valid_invitations}\n"
        f"待验证邀请: {stats.pending_invitations}\n总抽奖次数: {stats.tickets}\n"
        f"送达: 已确认 {deliveries.confirmed} / 待处理 {deliveries.pending} / "
        f"明确失败 {deliveries.failed} / 未知 {deliveries.unknown}"
    )
    return (
        text
        + _publication_status_note(campaign)
        + _management_details_text(details)
        + _purpose_text(deliveries)
    )


def _publication_status_note(campaign: object) -> str:
    if campaign.state is not CampaignState.PUBLISHING:
        return ""
    if campaign.presentation.get("publication_window_error"):
        return "\n\n发布延迟导致门槛不可完成: " + campaign.presentation["publication_window_error"]
    if campaign.cutoff_at <= utcnow():
        return "\n\n活动已过截止时间，但发布尚未完成。请取消此活动后创建新的抽奖。"
    return "\n\n全部参与群完整规则页和活动卡确认送达后才正式开始；投递异常请查看发布异常。"


def _purpose_text(deliveries):
    return "\n\n分用途投递（已撤销不算送达失败）：\n" + "\n".join(
        f"{row.label}：确认{row.confirmed} / 待处理{row.pending} / 明确失败{row.failed}"
        f" / 未知{row.unknown} / 已撤销{row.cancelled}"
        for row in deliveries.purposes
    )


def _management_details_text(details):
    frozen = "尚未形成快照" if details.frozen is None else f"{details.frozen}人"
    lines = [f"当前有效候选：{details.eligible}人；开奖快照名单：{frozen}"]
    labels = {
        "pending": "待处理",
        "confirmed": "已确认",
        "failed": "明确失败",
        "unknown": "待核查",
        "cancelled": "已撤销",
    }
    for i, target in enumerate(details.targets, 1):
        state = labels.get(target.card_state, target.card_state)
        rule = (
            (
                f"规则页确认{target.confirmed_pages}/{target.required_pages}，"
                f"明确失败{target.failed_pages}，未知{target.unknown_pages}"
            )
            if target.required_pages
            else "未查到独立规则页记录，请核对活动卡正文或原投放证据"
        )
        lines.append(f"{i}. {target.title}：活动卡{state}；{rule}")
    return "\n\n" + "\n".join(lines)
