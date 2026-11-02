"""Read-only prerequisite progress shared by member pages and reminders."""

from sqlalchemy import select

from tg_lottery.application.campaign_targets import qualification_targets
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import QualificationCheck
from tg_lottery.presentation.condition_progress import (
    condition_header,
    condition_label,
    condition_line,
)


def membership_progress_text(
    targets, observations, *, include_targets=True, source="上次检查结果"
) -> str:
    by_chat = {int(item["chat_id"]): item for item in observations}
    unique = {target.chat_id: target for target in targets}
    completed, lines = 0, []
    for chat_id, target in unique.items():
        item = by_chat.get(chat_id, {})
        label = condition_label(item)
        completed += int(label == "✅ 已完成")
        lines.append(condition_line(target, item))
    header = condition_header(completed, len(unique), source=source)
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
    latest = await membership_observations(session, participant.id)
    return membership_progress_text(targets, latest.values())


async def membership_observations(session, participant_id):
    checks = await session.scalars(
        select(QualificationCheck)
        .where(
            QualificationCheck.participant_id == participant_id,
            QualificationCheck.state != QualificationState.PENDING,
            QualificationCheck.phase.in_(["registration", "membership_event"]),
        )
        .order_by(QualificationCheck.observed_at.desc(), QualificationCheck.created_at.desc())
    )
    latest = {}
    for check in checks:
        for item in (check.observations or {}).get("chats", []):
            latest.setdefault(int(item["chat_id"]), item)
    return latest


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


async def pending_membership_chats(session, participant_id):
    latest = await membership_observations(session, participant_id)
    return tuple(
        chat
        for chat, fact in latest.items()
        if fact.get("observed_status") == "join_request_pending"
    )
