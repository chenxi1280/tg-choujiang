from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from tests.v30_helpers import FIRST, SECOND, START, rules_for, seed_v3, speech
from tg_lottery.application.participation_progress import membership_progress_text
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import (
    ActivityEvent,
    BaseActivityEvent,
    Campaign,
    DailyProgress,
    Participant,
    QualificationCheck,
    TelegramOutbox,
    TicketLedger,
)


def _targets():
    return tuple(
        SimpleNamespace(chat_id=chat, title=title, chat_type=kind)
        for chat, title, kind in [
            (FIRST, "A群", "supergroup"),
            (SECOND, "B群", "supergroup"),
            (-100125, "C频道", "channel"),
        ]
    )


def test_progress_counts_all_targets_and_keeps_pending_unknown_distinct():
    text = membership_progress_text(
        _targets(),
        [
            {"chat_id": FIRST, "state": "eligible"},
            {"chat_id": SECOND, "state": "ineligible", "observed_status": "join_request_pending"},
        ],
    )
    assert "已完成 1/3" in text
    assert "✅ 已完成 · 加入「A群」" in text
    assert "等待管理员批准" in text and "待核验 · 关注「C频道」" in text


async def _block(factory, campaign_id, participant_id):
    async with factory() as session, session.begin():
        participant = await session.get(Participant, participant_id)
        participant.counting_started_at = None
        participant.qualification_state = QualificationState.INELIGIBLE
        check = await session.scalar(select(QualificationCheck))
        check.state = QualificationState.INELIGIBLE
        check.observations = {
            "chats": [
                {"chat_id": FIRST, "state": "eligible"},
                {"chat_id": SECOND, "state": "ineligible"},
            ]
        }
        campaign = await session.get(Campaign, campaign_id)
        campaign.cutoff_at = START.replace(year=2030)


async def test_blocked_speech_never_counts_and_reminds_once_with_remaining_target(session_factory):
    cid, pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    await _block(session_factory, cid, pid)
    await speech(session_factory, count=3)
    async with session_factory() as session:
        notices = list(await session.scalars(select(TelegramOutbox)))
        assert len(notices) == 1
        assert "已完成 1/2" in notices[0].payload["text"]
        assert "未完成" in notices[0].payload["text"]
        assert notices[0].payload["delete_after_seconds"] == 15
        assert (await session.get(Participant, pid)).ticket_total_cache == 1
        for model in (ActivityEvent, BaseActivityEvent):
            assert await session.scalar(select(func.count(model.id)).where(model.accepted)) == 0
        assert await session.scalar(select(func.count(DailyProgress.id))) == 0
        assert await session.scalar(select(func.count(TicketLedger.id))) == 1


async def test_completion_notice_includes_remaining_group_progress(session_factory):
    rules = rules_for(
        "message",
        ("activity",),
        groups={
            str(FIRST): {"daily_messages": 2},
            str(SECOND): {"daily_messages": 2},
        },
    )
    _cid, pid = await seed_v3(session_factory, rules)
    await speech(session_factory, count=2)
    async with session_factory() as session:
        notices = list(await session.scalars(select(TelegramOutbox)))
        assert len(notices) == 1
        assert "0/2 条" in notices[0].payload["text"]
        assert "有效抽奖次数: 0" in notices[0].payload["text"]
        assert not (await session.get(Participant, pid)).admission_qualified


async def test_delayed_pre_start_messages_never_add_progress_or_bonus(session_factory):
    _cid, _pid = await seed_v3(session_factory, rules_for("message", ("activity",)))
    await speech(session_factory, count=2, at=START - timedelta(minutes=1))
    async with session_factory() as session:
        assert await session.scalar(select(func.count(DailyProgress.id))) == 0
        assert await session.scalar(select(func.count(TelegramOutbox.id))) == 0
        assert await session.scalar(select(func.count(TicketLedger.id))) == 1


async def test_later_departure_overrides_only_affected_target_in_saved_progress(session_factory):
    from functools import partial

    from tg_lottery.application.participant_qualification import mark_membership_ineligible
    from tg_lottery.application.participation_progress import saved_membership_progress
    from tg_lottery.infrastructure.db.uow import UnitOfWork

    cid, pid = await seed_v3(session_factory, rules_for("message"))
    async with session_factory() as session, session.begin():
        check = await session.scalar(select(QualificationCheck))
        check.observations = {
            "chats": [
                {"chat_id": FIRST, "state": "eligible"},
                {"chat_id": SECOND, "state": "eligible"},
            ]
        }
    await mark_membership_ineligible(
        partial(UnitOfWork, session_factory),
        campaign_id=cid,
        user_id=201,
        chat_id=SECOND,
        observed_at=START + timedelta(seconds=3),
    )
    async with session_factory() as session:
        text = await saved_membership_progress(
            session, await session.get(Campaign, cid), await session.get(Participant, pid)
        )
        assert "已完成 1/2" in text and "未完成 · 加入「B群」" in text


def test_long_progress_reminder_retains_whole_lines_and_full_detail_entry():
    from tg_lottery.application.participation_progress import brief_task_progress

    text = "\n".join(f"第{i}群: 今日0/10条" for i in range(100))
    brief = brief_task_progress(text, limit=100)
    assert "第0群: 今日0/10条" in brief
    assert "完整进度" in brief and "第99群" not in brief
