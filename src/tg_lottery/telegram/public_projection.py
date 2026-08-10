from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.errors import (
    AuthorizationDenied,
    InvalidStateTransition,
    InvitationValidationDeferred,
    RegionLockedError,
    SettlementBlocked,
    UserFacingError,
)

PUBLIC_TIME_FORMAT = "%Y年%m月%d日 %H:%M"
PUBLIC_TIMEZONE_LABELS = {
    "Asia/Shanghai": "北京时间",
    "Asia/Kuala_Lumpur": "马来西亚时间",
}
DEFAULT_PUBLIC_TIMEZONE_LABEL = "活动当地时间"

_CAMPAIGN_STATE_TEXT = {
    CampaignState.DRAFT: "草稿",
    CampaignState.PUBLISHING: "发布中",
    CampaignState.SCHEDULED: "等待开始",
    CampaignState.RUNNING: "进行中",
    CampaignState.PAUSED: "已暂停",
    CampaignState.SETTLING: "结算中",
    CampaignState.READY_TO_DRAW: "待开奖",
    CampaignState.DRAWING: "开奖中",
    CampaignState.DRAWN: "已开奖",
    CampaignState.ARCHIVED: "已归档",
    CampaignState.CANCELLED: "已取消",
}

_QUALIFICATION_STATE_TEXT = {
    QualificationState.PENDING: "待验证",
    QualificationState.ELIGIBLE: "符合条件",
    QualificationState.INELIGIBLE: "不符合条件",
    QualificationState.UNKNOWN: "暂无法确认",
}


def campaign_state_text(state: CampaignState) -> str:
    return _CAMPAIGN_STATE_TEXT[state]


def qualification_state_text(state: QualificationState) -> str:
    return _QUALIFICATION_STATE_TEXT[state]


def format_public_time(value: datetime | str, timezone: str) -> str:
    if not timezone:
        raise ValueError("public time requires an explicit timezone")
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unsupported public timezone: {timezone}") from exc
    localized = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
    label = public_timezone_label(timezone)
    return f"{localized.strftime(PUBLIC_TIME_FORMAT)} ({label})"


def public_timezone_label(timezone: str) -> str:
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unsupported public timezone: {timezone}") from exc
    return PUBLIC_TIMEZONE_LABELS.get(timezone, DEFAULT_PUBLIC_TIMEZONE_LABEL)


def public_error_text(exc: Exception, timezone: str) -> str:
    if isinstance(exc, UserFacingError):
        return exc.public_message
    if isinstance(exc, RegionLockedError):
        locked_until = format_public_time(exc.locked_until, timezone)
        return f"地区已锁定, 请于 {locked_until} 后修改。"
    if isinstance(exc, AuthorizationDenied):
        return "没有执行此操作的权限, 或页面授权已失效。"
    if isinstance(exc, InvalidStateTransition):
        return "活动状态已变化，请重新打开活动详情后再试。"
    if isinstance(exc, SettlementBlocked):
        return "活动仍在结算准备中，请稍后重试。"
    if isinstance(exc, InvitationValidationDeferred):
        return "参与资格暂时无法确认，请稍后重试。"
    return "系统暂时无法完成此操作, 请稍后重试。"
