from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.participant_records import (
    CampaignInvitationRecords,
    InvitationRecordFilter,
    InvitationRecordRow,
)
from tg_lottery.domain.enums import InvitationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
    format_public_time,
    qualification_state_text,
)

_GROUP_PAGE_SIZE = 6
_FILTER_LABELS = {
    "all": "全部",
    "pending": "等待中",
    "valid": "有效",
    "invalid": "未计入",
}

_INVITATION_STATE_TEXT = {
    InvitationState.ATTRIBUTION_CANDIDATE: "已申请入群，等待群管理员批准",
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
        f"🎟 {detail.campaign_name} #{detail.public_ref[:6]}\n"
        f"活动状态: {campaign_state_text(detail.state)}\n"
        f"参与资格: {qualification_state_text(detail.qualification)}\n"
        f"开奖时间: {format_public_time(detail.draw_at, detail.timezone)}\n\n"
        f"总抽奖次数: {detail.total}\n"
        f"基础次数: {detail.registration}\n"
        f"发言加成: {detail.activity}\n"
        f"邀请加成: {detail.invitation}",
        reply_markup=_record_keyboard(
            public_ref,
            bonus_enabled=detail.bonus_enabled,
            invite_enabled=detail.invite_enabled,
        ),
    )


async def show_invitation_records(
    message: Message,
    queries: object,
    user_id: int,
    *,
    public_ref: str,
    page: int,
    chat_id: int | None = None,
    group_page: int = 0,
    status_filter: InvitationRecordFilter = "all",
) -> None:
    records = await queries.campaign_invitation_records(
        user_id,
        public_ref,
        page=page,
        chat_id=chat_id,
        group_page=group_page,
        status_filter=status_filter,
    )
    if records is None:
        raise UserFacingError("未找到你在该活动中的邀请记录")
    lines = [_record_line(row, records.timezone) for row in records.rows]
    body = "\n\n".join(lines) if lines else "本活动暂无邀请记录"
    await message.answer(
        f"👥 {records.campaign_name} #{records.public_ref[:6]}｜{records.group_title}邀请记录\n"
        f"开奖：{format_public_time(records.draw_at, records.timezone)}\n"
        f"已生效 {records.valid}｜待验证 {records.pending}｜未生效 {records.invalid}\n\n"
        f"{body}",
        reply_markup=_invitation_keyboard(records),
    )


async def show_invitation_groups(
    message: Message,
    queries: object,
    user_id: int,
    *,
    public_ref: str,
    page: int,
) -> None:
    campaign = await queries.campaign_by_reference(public_ref)
    if campaign is None:
        raise UserFacingError("活动不存在或已经失效")
    rules = CampaignRules.model_validate(campaign.rules)
    groups = _invite_groups(campaign, rules)
    if not groups:
        await show_invitation_records(
            message,
            queries,
            user_id,
            public_ref=public_ref,
            page=page,
        )
        return
    progress = await queries.group_bonus_progress(campaign.id, user_id)
    if progress is None:
        raise UserFacingError("未找到你在该活动中的参与记录")
    progress_by_chat = {item.chat_id: item for item in progress.invitations}
    selected, total_pages, visible = _group_page(groups, page)
    lines = [
        f"👥 {campaign.name} #{campaign.public_ref[:6]}｜我的邀请记录",
        f"开奖：{format_public_time(campaign.draw_at, campaign.timezone)}",
        "",
        "请选择一个群查看状态和未计入原因：",
    ]
    rows = []
    for group in visible:
        item = progress_by_chat.get(int(group["id"]))
        valid = int(getattr(item, "valid_invites", 0))
        awarded = int(getattr(item, "awarded_tickets", 0))
        lines.append(f"• {group['title']}｜有效 {valid}｜+{awarded} 次")
        rows.append(_record_group_button(campaign.public_ref, group, group_page=selected))
    navigation = _group_page_buttons(campaign.public_ref, selected, total_pages)
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="返回邀请活动", callback_data="nav:invites")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


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


def _record_keyboard(
    public_ref: str, *, bonus_enabled: bool, invite_enabled: bool
) -> InlineKeyboardMarkup:
    rows = []
    if bonus_enabled:
        rows.append([InlineKeyboardButton(text="我的加成", callback_data=f"m:bo:{public_ref}")])
    if invite_enabled:
        rows.append(
            [
                InlineKeyboardButton(
                    text="查看本期邀请记录",
                    callback_data=f"member:invites:{public_ref}:0",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="返回参与列表", callback_data="nav:join")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _invitation_keyboard(records: CampaignInvitationRecords) -> InlineKeyboardMarkup:
    rows = []
    if records.group_chat_id is not None:
        rows.append(_filter_row(records))
    pages = []
    if records.page > 0:
        pages.append(
            InlineKeyboardButton(
                text="上一页",
                callback_data=_record_page_callback(records, records.page - 1),
            )
        )
    if records.page + 1 < records.total_pages:
        pages.append(
            InlineKeyboardButton(
                text="下一页",
                callback_data=_record_page_callback(records, records.page + 1),
            )
        )
    if pages:
        rows.append(pages)
    rows.append(
        [
            InlineKeyboardButton(
                text="查看本期抽奖次数",
                callback_data=f"member:tickets:{records.public_ref}",
            )
        ]
    )
    back = (
        f"member:invites:{records.public_ref}:{records.group_page}"
        if records.group_chat_id is not None
        else "nav:join"
    )
    back_label = "返回群列表" if records.group_chat_id is not None else "返回参与列表"
    rows.append([InlineKeyboardButton(text=back_label, callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _filter_row(records: CampaignInvitationRecords) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=("✅ " if key == records.status_filter else "") + label,
            callback_data=(
                f"m:ir:{records.public_ref}:{records.group_chat_id}:{records.group_page}:{key}:0"
            ),
        )
        for key, label in _FILTER_LABELS.items()
    ]


def _record_page_callback(records: CampaignInvitationRecords, page: int) -> str:
    if records.group_chat_id is None:
        return f"member:invites:{records.public_ref}:{page}"
    return (
        f"m:ir:{records.public_ref}:{records.group_chat_id}:{records.group_page}:"
        f"{records.status_filter}:{page}"
    )


def _invite_groups(campaign: object, rules: CampaignRules) -> tuple[dict[str, object], ...]:
    presentation = getattr(campaign, "presentation", {}) or {}
    return tuple(
        {"id": int(item["id"]), "title": str(item.get("title") or item["id"])}
        for item in presentation.get("participation_groups") or []
        if item.get("id") is not None and rules.invite_rule_for(int(item["id"])) is not None
    )


def _group_page(
    groups: tuple[dict[str, object], ...], page: int
) -> tuple[int, int, tuple[dict[str, object], ...]]:
    total_pages = max(1, (len(groups) + _GROUP_PAGE_SIZE - 1) // _GROUP_PAGE_SIZE)
    selected = min(max(0, page), total_pages - 1)
    start = selected * _GROUP_PAGE_SIZE
    return selected, total_pages, groups[start : start + _GROUP_PAGE_SIZE]


def _record_group_button(
    public_ref: str, group: dict[str, object], *, group_page: int
) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            text=str(group["title"]),
            callback_data=f"m:ir:{public_ref}:{group['id']}:{group_page}:all:0",
        )
    ]


def _group_page_buttons(public_ref: str, page: int, total_pages: int) -> list[InlineKeyboardButton]:
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="上一页", callback_data=f"member:invites:{public_ref}:{page - 1}"
            )
        )
    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="下一页", callback_data=f"member:invites:{public_ref}:{page + 1}"
            )
        )
    return buttons
