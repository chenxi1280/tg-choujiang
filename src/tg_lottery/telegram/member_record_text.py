from tg_lottery.application.participant_records import ParticipantCampaignDetail
from tg_lottery.application.participant_results import ParticipantAward
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.public_projection import (
    campaign_history_state_text,
    format_public_time,
    qualification_state_text,
)


def participant_record_pages(detail: ParticipantCampaignDetail) -> tuple[str, ...]:
    blocks = [
        f"🎟 {detail.campaign_name}\n"
        f"活动状态: {_record_state_text(detail)}\n"
        f"参与资格: {qualification_state_text(detail.qualification)}\n"
        f"计划开奖时间: {format_public_time(detail.draw_at, detail.timezone)}",
    ]
    result = _result_summary(detail)
    if result:
        blocks.append(result)
    if detail.draw_result is not None:
        blocks.extend(_award_text(award) for award in detail.draw_result.awards)
    blocks.append(_ticket_summary(detail))
    return _record_pages(blocks)


def _record_state_text(detail: ParticipantCampaignDetail) -> str:
    kind = "drawn" if detail.draw_result is not None else None
    if kind is None and detail.outcome is not None:
        kind = detail.outcome.kind
    return campaign_history_state_text(detail.state, kind)


def _result_summary(detail: ParticipantCampaignDetail) -> str:
    if detail.state not in {CampaignState.DRAWN, CampaignState.ARCHIVED, CampaignState.CANCELLED}:
        return ""
    result = detail.draw_result
    if result is None:
        return _missing_result_summary(detail)
    if result.frozen_tickets is None:
        status = "未进入最终开奖名单"
    else:
        status = "🎉 已中奖" if result.awards else "未中奖"
    lines = [
        f"开奖结果：{status}",
        f"实际开奖时间：{format_public_time(result.completed_at, detail.timezone)}",
    ]
    if result.frozen_tickets is not None:
        lines.append(f"最终开奖次数：{result.frozen_tickets}（以已保存开奖快照为准）")
    if result.awards:
        contact = f"@{result.issuer_username}" if result.issuer_username else result.issuer_name
        lines.append(f"发奖人员：{contact}")
    return "\n".join(lines)


def _missing_result_summary(detail: ParticipantCampaignDetail) -> str:
    if detail.outcome is not None and detail.outcome.kind == "cancelled":
        return "开奖结果：活动已取消，未开奖。"
    if detail.state in {CampaignState.ARCHIVED, CampaignState.CANCELLED}:
        return "历史开奖结果未确认：未找到已完成的开奖记录或本活动取消记录，请联系活动创建者核查。"
    return "开奖结果：未找到已完成的开奖记录，请联系活动创建者核查。"


def _award_text(award: ParticipantAward) -> str:
    return (
        f"🏆 第{award.prize_position}顺位 {award.prize_name}（名额 {award.prize_slot_no}）\n"
        f"领奖方式：{award.claim_instructions}"
    )


def _ticket_summary(detail: ParticipantCampaignDetail) -> str:
    active = detail.state in {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
        CampaignState.PAUSED,
    }
    label = "累计次数（含暂存）" if active else "历史累计次数（账本记录）"
    lines = [
        f"{label}: {detail.total}",
        f"基础次数: {detail.registration}",
        f"发言加成: {detail.activity}",
        f"邀请加成: {detail.invitation}",
        detail.base_progress,
    ]
    if not active and not detail.base_qualified:
        lines.append("基础门槛未达标，以上历史累计不代表最终开奖次数。")
    return "\n".join(lines)


def _record_pages(blocks: list[str]) -> tuple[str, ...]:
    from tg_lottery.telegram.panel_pagination import text_pages

    return text_pages("\n\n".join(blocks))
