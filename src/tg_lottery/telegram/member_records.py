from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.participant_records import (
    CampaignInvitationRecords,
    InvitationRecordRow,
)
from tg_lottery.domain.enums import InvitationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
    format_public_time,
    qualification_state_text,
)

_INVITATION_STATE_TEXT = {
    InvitationState.ATTRIBUTION_CANDIDATE: "已归因，等待入群",
    InvitationState.JOINED_PENDING: "已入群，等待验证",
    InvitationState.CHECKING: "验证中",
    InvitationState.VALID: "已生效",
    InvitationState.LEFT_BEFORE_VALID: "已失效",
    InvitationState.INVALID: "未生效",
    InvitationState.VALIDATION_UNKNOWN: "验证异常",
    InvitationState.EXPIRED_UNQUALIFIED: "未达标",
}
_FINAL_REASON_TEXT = {
    "existing_member": "此前已加入过该群",
    "after_bonus_deadline": "超过本期最晚计奖入群时间",
    "left_before_cutoff": "截止前已退群",
    "left_before_valid": "验证完成前已退群",
    "retention_after_cutoff": "留群验证时间超过截止时间",
    "campaign_not_running": "活动已停止计奖",
    "campaign_closed": "活动已关闭",
    "validation_unknown": "Telegram 暂时无法确认",
    "invitee_activity_not_met": "被邀请人未达到发言要求",
}


async def show_ticket_record(
    message: Message, queries: object, user_id: int, *, public_ref: str
) -> None:
    detail = await queries.participant_campaign_detail(user_id, public_ref)
    if detail is None:
        raise UserFacingError("未找到你在该活动中的参与记录")
    await message.answer(
        f"🎟 {detail.campaign_name}\n"
        f"活动状态: {campaign_state_text(detail.state)}\n"
        f"参与资格: {qualification_state_text(detail.qualification)}\n"
        f"开奖时间: {format_public_time(detail.draw_at, detail.timezone)}\n\n"
        f"总抽奖次数: {detail.total}\n"
        f"基础次数: {detail.registration}\n"
        f"发言加成: {detail.activity}\n"
        f"邀请加成: {detail.invitation}",
        reply_markup=_record_keyboard(public_ref),
    )


async def show_invitation_records(
    message: Message,
    queries: object,
    user_id: int,
    *,
    public_ref: str,
    page: int,
) -> None:
    records = await queries.campaign_invitation_records(user_id, public_ref, page)
    if records is None:
        raise UserFacingError("未找到你在该活动中的邀请记录")
    lines = [_record_line(row, records.timezone) for row in records.rows]
    body = "\n\n".join(lines) if lines else "本活动暂无邀请记录"
    await message.answer(
        f"👥 {records.campaign_name}｜邀请记录\n"
        f"已生效 {records.valid}｜待验证 {records.pending}｜未生效 {records.invalid}\n\n"
        f"{body}",
        reply_markup=_invitation_keyboard(records),
    )


def _record_line(row: InvitationRecordRow, timezone: str) -> str:
    identity = _masked_identity(row.invitee_name, row.invitee_user_id)
    status = _INVITATION_STATE_TEXT[row.state]
    reason = _FINAL_REASON_TEXT.get(row.final_reason or "")
    result = f"{status}（{reason}）" if reason else status
    observed = format_public_time(row.observed_at, timezone)
    return f"• {row.group_title}｜{identity}\n  {result}｜{observed}"


def _masked_identity(name: str | None, user_id: int) -> str:
    normalized = (name or "").strip()
    masked = (
        normalized[0] + "*" * min(3, max(1, len(normalized) - 1)) if normalized else "Telegram 用户"
    )
    return f"{masked} · ID尾号 {str(user_id)[-4:]}"


def _record_keyboard(public_ref: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="查看本期邀请记录",
                    callback_data=f"member:invites:{public_ref}:0",
                )
            ],
            [InlineKeyboardButton(text="返回参与列表", callback_data="nav:join")],
        ]
    )


def _invitation_keyboard(records: CampaignInvitationRecords) -> InlineKeyboardMarkup:
    pages = []
    if records.page > 0:
        pages.append(
            InlineKeyboardButton(
                text="上一页",
                callback_data=f"member:invites:{records.public_ref}:{records.page - 1}",
            )
        )
    if records.page + 1 < records.total_pages:
        pages.append(
            InlineKeyboardButton(
                text="下一页",
                callback_data=f"member:invites:{records.public_ref}:{records.page + 1}",
            )
        )
    rows = [pages] if pages else []
    rows.append(
        [
            InlineKeyboardButton(
                text="查看本期抽奖次数",
                callback_data=f"member:tickets:{records.public_ref}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="返回参与列表", callback_data="nav:join")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
