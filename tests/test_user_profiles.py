from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import func, select

from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.infrastructure.db import UserRegionChange
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_region_edit_window_lock_cycle_and_audit(session_factory) -> None:
    service = UserProfileService(partial(UnitOfWork, session_factory))
    started_at = datetime(2026, 8, 8, 0, 0, tzinfo=UTC)

    first = await service.set_region(202, "河南", started_at)
    edited = await service.set_region(202, "北京", started_at + timedelta(minutes=4))
    with pytest.raises(ValueError, match="地区已锁定"):
        await service.set_region(202, "上海", started_at + timedelta(minutes=6))
    renewed = await service.set_region(202, "上海", started_at + timedelta(days=30))

    assert first.region_selected_at == started_at
    assert edited.region == "北京"
    assert renewed.region == "上海"
    async with session_factory() as session:
        changes = await session.scalar(select(func.count(UserRegionChange.id)))
        latest = await session.scalar(
            select(UserRegionChange).order_by(UserRegionChange.changed_at.desc()).limit(1)
        )
    assert changes == 3
    assert latest is not None and latest.old_region == "北京"


async def test_last_draft_only_persists_supported_business_fields(session_factory) -> None:
    service = UserProfileService(partial(UnitOfWork, session_factory))
    now = datetime.now(UTC)

    await service.save_last_draft(
        202,
        {"title": "周末福利", "draw_mode": "full", "panel_message_id": 99},
        now,
    )

    profile = await service.get(202)
    assert profile.last_draft == {"title": "周末福利", "draw_mode": "full"}
    assert await service.recent_last_draft(202, now + timedelta(hours=23)) == profile.last_draft
    assert await service.recent_last_draft(202, now + timedelta(hours=25)) == {}
