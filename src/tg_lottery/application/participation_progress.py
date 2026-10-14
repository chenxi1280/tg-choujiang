"""Read-only prerequisite progress shared by member pages and reminders."""

from sqlalchemy import select

from tg_lottery.application.campaign_targets import qualification_targets
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import QualificationCheck

COUNTING_NOTICE = (
    "全部入群、关注及其他前置条件通过并登记后,才开始统计本场发言;此前发言不补算。"
    "行为任务未完成时加成暂存,有效抽奖次数为0。"
)


def membership_progress_text(targets, observations, *, include_targets=True) -> str:
    by_chat = {int(item["chat_id"]): item for item in observations}
    unique = {target.chat_id: target for target in targets}
    completed, lines = 0, []
    for chat_id, target in unique.items():
        item = by_chat.get(chat_id, {})
        label = _condition_label(item)
        completed += int(label == "✅ 已完成")
        action = "关注" if target.chat_type == "channel" else "加入"
        lines.append(f"{label} · {action}「{target.title}」")
    header = f"入群/关注条件进度:已完成 {completed}/{len(unique)} 项(以核验记录为准)"
    return "\n".join([header, *(lines if include_targets else [])]) if unique else ""


async def latest_membership_check(session, participant_id):
    return await session.scalar(
        select(QualificationCheck)
        .where(
            QualificationCheck.participant_id == participant_id,
            QualificationCheck.state != QualificationState.PENDING,
            QualificationCheck.phase.in_(["registration", "membership_event"]),
        )
        .order_by(QualificationCheck.observed_at.desc(), QualificationCheck.created_at.desc())
        .limit(1)
    )


async def saved_membership_progress(session, campaign, participant) -> str:
    targets = await qualification_targets(session, campaign)
    if not targets:
        return ""
    checks = await session.scalars(
        select(QualificationCheck)
        .where(
            QualificationCheck.participant_id == participant.id,
            QualificationCheck.state != QualificationState.PENDING,
            QualificationCheck.phase.in_(["registration", "membership_event"]),
        )
        .order_by(QualificationCheck.observed_at.desc(), QualificationCheck.created_at.desc())
    )
    latest = {}
    for check in checks:
        for item in (check.observations or {}).get("chats", []):
            latest.setdefault(int(item["chat_id"]), item)
    return membership_progress_text(targets, latest.values())


def _condition_label(item):
    if item.get("observed_status") == "join_request_pending":
        return "⏳ 等待管理员批准"
    return {"eligible": "✅ 已完成", "ineligible": "⬜ 未完成"}.get(
        str(item.get("state", "unknown")), "⏳ 待核验"
    )


def brief_task_progress(text: str, *, limit: int = 2000) -> str:
    lines, units = [], 0
    for line in text.splitlines():
        size = len((line + "\n").encode("utf-16-le")) // 2
        if units + size > limit:
            lines.append("其余任务与完整进度请打开「查看我的进度」。")
            break
        lines.append(line)
        units += size
    return "\n".join(lines)
