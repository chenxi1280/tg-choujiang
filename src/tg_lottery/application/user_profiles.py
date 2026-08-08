from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.infrastructure.db import UserProfile, UserRegionChange

REGION_EDIT_WINDOW = timedelta(minutes=5)
REGION_LOCK_PERIOD = timedelta(days=30)
LAST_DRAFT_TTL = timedelta(hours=24)
_DRAFT_KEYS = frozenset(
    {
        "chat_id",
        "group_title",
        "timezone",
        "title",
        "draw_at",
        "draw_mode",
        "participant_target",
        "claim_instructions",
        "prizes",
        "activity_preset",
        "invite_preset",
        "required_channel_id",
        "required_chat_ids",
        "required_chat_refs",
        "cover_file_id",
        "description",
        "username_required",
        "allowed_regions",
        "issuer",
        "issuer_name",
        "luck_mode",
        "message_tips",
    }
)


@dataclass(frozen=True)
class ProfileSnapshot:
    user_id: int
    region: str | None
    region_selected_at: datetime | None
    region_editable_until: datetime | None
    region_locked_until: datetime | None
    last_draft: dict[str, Any]
    last_draft_saved_at: datetime | None


class UserProfileService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get(self, user_id: int) -> ProfileSnapshot:
        async with self._uow_factory() as uow:
            profile = await uow.session.get(UserProfile, user_id)
            return _snapshot(profile, user_id)

    async def set_region(self, user_id: int, region: str, now: datetime) -> ProfileSnapshot:
        async with self._uow_factory() as uow:
            profile = await uow.session.get(UserProfile, user_id, with_for_update=True)
            if profile is None:
                profile = UserProfile(user_id=user_id, last_draft={})
                uow.session.add(profile)
            old_region = profile.region
            _set_region(profile, region, now)
            uow.session.add(
                UserRegionChange(
                    user_id=user_id,
                    old_region=old_region,
                    new_region=region,
                    changed_at=now,
                )
            )
            await uow.session.flush()
            return _snapshot(profile, user_id)

    async def save_last_draft(self, user_id: int, data: dict[str, Any], now: datetime) -> None:
        async with self._uow_factory() as uow:
            profile = await uow.session.get(UserProfile, user_id, with_for_update=True)
            if profile is None:
                profile = UserProfile(user_id=user_id, last_draft={})
                uow.session.add(profile)
            profile.last_draft = {key: value for key, value in data.items() if key in _DRAFT_KEYS}
            profile.last_draft_saved_at = now

    async def recent_last_draft(self, user_id: int, now: datetime) -> dict[str, Any]:
        profile = await self.get(user_id)
        if profile.last_draft_saved_at is None:
            return {}
        if now - profile.last_draft_saved_at > LAST_DRAFT_TTL:
            return {}
        return dict(profile.last_draft)


def _set_region(profile: UserProfile, region: str, now: datetime) -> None:
    if profile.region is None or _new_region_cycle(profile, now):
        profile.region_editable_until = now + REGION_EDIT_WINDOW
        profile.region_locked_until = now + REGION_LOCK_PERIOD
    elif not _within_edit_window(profile, now):
        raise ValueError(f"地区已锁定, 请于 {profile.region_locked_until:%Y-%m-%d %H:%M} 后修改")
    profile.region = region
    profile.region_selected_at = now


def _new_region_cycle(profile: UserProfile, now: datetime) -> bool:
    return bool(profile.region_locked_until and now >= profile.region_locked_until)


def _within_edit_window(profile: UserProfile, now: datetime) -> bool:
    return bool(profile.region_editable_until and now <= profile.region_editable_until)


def _snapshot(profile: UserProfile | None, user_id: int) -> ProfileSnapshot:
    if profile is None:
        return ProfileSnapshot(user_id, None, None, None, None, {}, None)
    return ProfileSnapshot(
        profile.user_id,
        profile.region,
        profile.region_selected_at,
        profile.region_editable_until,
        profile.region_locked_until,
        dict(profile.last_draft),
        profile.last_draft_saved_at,
    )
