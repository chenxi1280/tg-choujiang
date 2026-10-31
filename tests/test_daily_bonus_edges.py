import asyncio
from datetime import timedelta
from functools import partial

import pytest
from sqlalchemy import select

from tests.test_daily_bonus_integration import daily_rules, totals
from tests.test_daily_bonus_policy import current_draft
from tests.v30_helpers import FIRST, SECOND, START, seed_v3, speech
from tg_lottery.application.activity import ActivityService, MessageObservation
from tg_lottery.application.group_bonus_progress import load_group_bonus_progress
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import Campaign
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.presentation.daily_bonus import daily_bonus_dates, daily_bonus_progress_text
from tg_lottery.telegram import creator_group_bonus as bonus


async def test_beijing_midnight_not_utc_day_starts_second_day(session_factory):
    cid, pid = await seed_v3(session_factory, daily_rules())
    midnight = START.replace(hour=16, minute=0, second=0)
    await speech(session_factory, count=3, at=midnight - timedelta(seconds=4))
    assert await totals(session_factory, pid) == 1
    first = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=midnight - timedelta(seconds=1)
    )
    assert first.daily_bonus.first_day
    await speech(session_factory, count=3, at=midnight, first_id=100)
    assert await totals(session_factory, pid) == 2


async def test_non_double_same_second_counts_only_messages_after_gate(session_factory):
    _, pid = await seed_v3(session_factory, daily_rules(double=False, threshold=2, reward=1))
    service = ActivityService(partial(UnitOfWork, session_factory))
    at = START + timedelta(days=1)
    for number in range(1, 4):
        message = MessageObservation(FIRST, number, 201, at, f"同秒消息{number}")
        await service.record_message(message)
        await service.record_message(message)
        assert await totals(session_factory, pid) == (2 if number == 3 else 1)


async def test_concurrent_group_completion_awards_one_ticket(session_factory):
    _, pid = await seed_v3(session_factory, daily_rules(groups=(FIRST, SECOND)))
    at = START + timedelta(days=1)
    await asyncio.gather(
        speech(session_factory, chat_id=FIRST, count=4, at=at),
        speech(session_factory, chat_id=SECOND, count=4, at=at, first_id=100),
    )
    assert await totals(session_factory, pid) == 2


@pytest.mark.parametrize("state", [CampaignState.PAUSED, CampaignState.CANCELLED])
async def test_paused_and_cancelled_views_do_not_prompt_new_rewards(session_factory, state):
    cid, pid = await seed_v3(session_factory, daily_rules())
    at = START + timedelta(days=1)
    await speech(session_factory, count=3, at=at)
    async with session_factory() as session, session.begin():
        campaign = await session.get(Campaign, cid)
        campaign.state = state
    await speech(session_factory, count=3, at=at + timedelta(hours=1), first_id=100)
    assert await totals(session_factory, pid) == 2
    progress = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=at + timedelta(hours=2)
    )
    text = daily_bonus_progress_text(progress.daily_bonus, names={})
    assert "条件达标后自动发放" not in text
    assert ("暂停" if state == CampaignState.PAUSED else "历史累计1张") in text


async def test_final_cutoff_keeps_awarded_history_and_stops_new_messages(session_factory):
    cid, pid = await seed_v3(session_factory, daily_rules())
    async with session_factory() as session:
        cutoff = await session.scalar(select(Campaign.cutoff_at).where(Campaign.id == cid))
    await speech(session_factory, count=3, at=cutoff - timedelta(seconds=4))
    assert await totals(session_factory, pid) == 2
    await speech(session_factory, count=3, at=cutoff, first_id=100)
    assert await totals(session_factory, pid) == 2
    progress = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=cutoff + timedelta(days=1)
    )
    assert "历史累计1张" in daily_bonus_progress_text(progress.daily_bonus, names={})


def test_copy_cancel_restores_saved_double_count_and_unsaved_changes_cannot_copy():
    data = bonus.apply_action(current_draft(), f"draft_bonus_activity_group:{FIRST}")
    data = bonus.apply_action(data, "draft_bonus_activity_save")
    data = bonus.apply_action(data, f"draft_bonus_activity_group:{FIRST}")
    changed = bonus.apply_action(data, "draft_bonus_double:off")
    with pytest.raises(UserFacingError, match="请先保存"):
        bonus.apply_action(changed, "draft_bonus_copy_start")
    copying = bonus.apply_action(data, "draft_bonus_copy_start")
    restored = bonus.apply_action(copying, "draft_bonus_copy_cancel")
    assert restored["bonus_working_copy"]["double_count"]
    assert "双算" in bonus.editor_view(restored, restored["bonus_working_copy"])[0]


def test_single_day_activity_preview_states_zero_available_tickets():
    assert "0张" in daily_bonus_dates(START, START + timedelta(hours=1))
    assert "最多2张" in daily_bonus_dates(START, START + timedelta(days=2))


async def test_invitation_gate_does_not_block_daily_speech_ticket(session_factory):
    from tests.v30_helpers import rules_for
    from tg_lottery.domain.rules import CampaignRules
    from tg_lottery.infrastructure.db import Participant

    raw = daily_rules().model_dump(mode="json")
    raw["admission"] = rules_for("invite").admission.model_dump(mode="json")
    cid, pid = await seed_v3(session_factory, CampaignRules.model_validate(raw))
    at = START + timedelta(days=1)
    await speech(session_factory, count=3, at=at)
    assert await totals(session_factory, pid) == 2
    async with session_factory() as session:
        assert not (await session.get(Participant, pid)).admission_qualified
    state = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=at + timedelta(hours=1)
    )
    assert state.daily_bonus.awarded == 1


async def test_late_registration_does_not_count_earlier_messages(session_factory):
    from tg_lottery.infrastructure.db import Participant

    cid, pid = await seed_v3(session_factory, daily_rules())
    at = START + timedelta(days=1)
    async with session_factory() as session, session.begin():
        participant = await session.get(Participant, pid)
        participant.counting_started_at = at + timedelta(hours=1)
    await speech(session_factory, count=3, at=at)
    assert await totals(session_factory, pid) == 1
    await speech(session_factory, count=3, at=at + timedelta(hours=2), first_id=100)
    assert await totals(session_factory, pid) == 2
    state = await load_group_bonus_progress(
        session_factory, cid, 201, observed_at=at + timedelta(hours=3)
    )
    assert state.daily_bonus.groups[0].messages == 3


async def test_stored_new_rules_reject_images_missing_bonus_or_period_capability(
    session_factory, monkeypatch
):
    import os

    from tg_lottery.rule_capabilities import SUPPORTED_RULE_VERSIONS, check_database

    await seed_v3(session_factory, daily_rules(mode="period"))
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    await check_database(SUPPORTED_RULE_VERSIONS)
    for capability in ("bonus:daily-after-start-v1", "message-admission:period-v1"):
        with pytest.raises(RuntimeError, match=capability):
            await check_database(SUPPORTED_RULE_VERSIONS - {capability})
