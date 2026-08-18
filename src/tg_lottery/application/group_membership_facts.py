from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from tg_lottery.infrastructure.db import (
    CampaignParticipationGroup,
    GroupMembershipFact,
    Invitation,
    InviteLink,
)


@dataclass(frozen=True)
class MembershipObservation:
    tracked: bool
    first_for_link: bool


async def observe_join(
    session: object,
    *,
    link: InviteLink,
    user_id: int,
    observed_at: datetime,
) -> MembershipObservation:
    target = await _link_target(session, link)
    if target is None:
        return MembershipObservation(False, True)
    fact_id = uuid4()
    inserted = await session.scalar(
        insert(GroupMembershipFact)
        .values(
            id=fact_id,
            group_id=target.group_id,
            user_id=user_id,
            first_observed_member_at=observed_at,
            first_source_invite_link_id=link.id,
            last_observed_state="member",
            last_observed_at=observed_at,
            certainty="confirmed",
        )
        .on_conflict_do_nothing(index_elements=["group_id", "user_id"])
        .returning(GroupMembershipFact.id)
    )
    fact = await _locked_fact(session, target.group_id, user_id)
    if fact is None:
        raise RuntimeError("group membership fact insert disappeared")
    fact.last_observed_state = "member"
    fact.last_observed_at = max(fact.last_observed_at, observed_at)
    return MembershipObservation(
        True, inserted == fact_id or fact.first_source_invite_link_id == link.id
    )


async def observe_existing_member(
    session: object,
    *,
    link: InviteLink,
    user_id: int,
    observed_at: datetime,
) -> None:
    target = await _link_target(session, link)
    if target is None:
        return
    await session.execute(
        insert(GroupMembershipFact)
        .values(
            id=uuid4(),
            group_id=target.group_id,
            user_id=user_id,
            first_observed_member_at=observed_at,
            first_source_invite_link_id=None,
            last_observed_state="member",
            last_observed_at=observed_at,
            certainty="confirmed",
        )
        .on_conflict_do_update(
            index_elements=["group_id", "user_id"],
            set_={
                "last_observed_state": "member",
                "last_observed_at": observed_at,
                "certainty": "confirmed",
            },
        )
    )


async def mark_invitation_member_left(
    session: object, invitation: Invitation, *, observed_at: datetime
) -> None:
    target = await _invitation_target(session, invitation)
    if target is None:
        return
    fact = await _locked_fact(session, target.group_id, invitation.invitee_user_id)
    if fact is None:
        return
    fact.last_observed_state = "left"
    fact.last_observed_at = max(fact.last_observed_at, observed_at)


async def first_source_matches_invitation(session: object, invitation: Invitation) -> bool:
    target = await _invitation_target(session, invitation)
    if target is None:
        return True
    fact = await _locked_fact(session, target.group_id, invitation.invitee_user_id)
    return fact is not None and fact.first_source_invite_link_id == invitation.invite_link_id


async def has_membership_history(session: object, *, link: InviteLink, user_id: int) -> bool:
    target = await _link_target(session, link)
    if target is None:
        return False
    return await _locked_fact(session, target.group_id, user_id) is not None


async def _link_target(session: object, link: InviteLink) -> CampaignParticipationGroup | None:
    if link.participation_group_id is None:
        return None
    target = await session.get(CampaignParticipationGroup, link.participation_group_id)
    if target is None or target.campaign_id != link.campaign_id:
        raise ValueError("invite link participation group is missing")
    return target


async def _invitation_target(
    session: object, invitation: Invitation
) -> CampaignParticipationGroup | None:
    if invitation.participation_group_id is None:
        return None
    target = await session.get(CampaignParticipationGroup, invitation.participation_group_id)
    if target is None or target.campaign_id != invitation.campaign_id:
        raise ValueError("invitation participation group is missing")
    return target


async def _locked_fact(
    session: object, group_id: object, user_id: int
) -> GroupMembershipFact | None:
    return await session.scalar(
        select(GroupMembershipFact)
        .where(
            GroupMembershipFact.group_id == group_id,
            GroupMembershipFact.user_id == user_id,
        )
        .with_for_update()
    )
