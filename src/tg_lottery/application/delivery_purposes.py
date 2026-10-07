from dataclasses import dataclass

from sqlalchemy import func, select

from tg_lottery.domain.enums import OutboxState
from tg_lottery.infrastructure.db import TelegramOutbox

PURPOSE_LABELS = {
    "send_campaign_card": "参与群活动卡",
    "send_campaign_rules": "完整规则页",
    "send_draw_result": "开奖结果群公告",
    "send_winner_notice": "中奖私信",
    "create_invite_link": "个人邀请入口创建",
    "send_invite_link": "个人邀请入口私信",
    "create_required_chat_invite": "群/频道加入入口创建",
    "send_progress_notice": "必要进度通知",
    "send_temporary_reminder": "临时操作提醒",
    "edit_campaign_card_state": "活动卡状态更新",
    "edit_campaign_card_cancelled": "活动卡取消同步",
    "revoke_invite_link": "邀请入口撤销",
    "revoke_required_chat_invite": "加入入口撤销",
}


@dataclass(frozen=True)
class DeliveryPurpose:
    label: str
    confirmed: int
    pending: int
    failed: int
    unknown: int
    cancelled: int


async def load_delivery_purposes(session, aggregate_ids):
    rows = await session.execute(
        select(TelegramOutbox.operation, TelegramOutbox.state, func.count(TelegramOutbox.id))
        .where(TelegramOutbox.aggregate_id.in_(aggregate_ids))
        .group_by(TelegramOutbox.operation, TelegramOutbox.state)
        .order_by(TelegramOutbox.operation)
    )
    operations = {}
    for operation, state, count in rows:
        operations.setdefault(operation, {})[state] = int(count)
    return tuple(_purpose(operation, counts) for operation, counts in operations.items())


def _purpose(operation, counts):
    return DeliveryPurpose(
        PURPOSE_LABELS.get(operation, operation),
        counts.get(OutboxState.CONFIRMED, 0),
        sum(
            counts.get(state, 0)
            for state in (OutboxState.PENDING, OutboxState.CLAIMED, OutboxState.RETRY_SCHEDULED)
        ),
        counts.get(OutboxState.PRE_ACCEPT_FAILED, 0),
        counts.get(OutboxState.UNKNOWN_HOLD, 0),
        counts.get(OutboxState.CANCELLED, 0),
    )
