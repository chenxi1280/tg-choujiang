from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import or_, select

from tg_lottery.application.campaign_targets import QualificationTarget
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.request_context import current_update_id
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.rules import CampaignRules, canonical_hash
from tg_lottery.infrastructure.db import (
    Campaign,
    CampaignParticipationGroup,
    Group,
    Participant,
    QualificationCheck,
    QualificationTargetObservation,
)

_REGISTRATION_STATES = frozenset({"scheduled", "running"})


class QualificationResultLike(Protocol):
    state: QualificationState
    observed_at: datetime
    observed_status: str | None
    error_code: str | None
    observations: tuple[dict[str, Any], ...]


def apply_check_result(check: QualificationCheck, result: QualificationResultLike) -> None:
    check.state = result.state
    check.observed_status = result.observed_status
    check.error_code = result.error_code
    check.observations = {"chats": list(result.observations)}
    check.observed_at = result.observed_at
    check.participation_groups_state = kind_state(result.observations, "participation_group")
    check.requirements_state = kind_state(result.observations, "requirement")


def build_draw_check(
    participant: Participant,
    *,
    state: QualificationState,
    observed_at: datetime,
    observed_status: str | None,
    error_code: str | None,
    observations: tuple[dict[str, Any], ...],
) -> QualificationCheck:
    return QualificationCheck(
        participant_id=participant.id,
        phase="draw",
        state=state,
        observed_status=observed_status,
        error_code=error_code,
        observations={"chats": list(observations)},
        observed_at=observed_at,
        request_hash=canonical_hash({"participant_id": str(participant.id), "phase": "draw"}),
        source_update_id=current_update_id(),
        participation_groups_state=kind_state(observations, "participation_group"),
        requirements_state=kind_state(observations, "requirement"),
    )


def store_target_observations(
    session: object,
    check: QualificationCheck,
    result: QualificationResultLike,
) -> None:
    latest = {int(item["chat_id"]): item for item in result.observations}
    for chat_id, item in latest.items():
        target_kind = str(item.get("target_kind") or "requirement")
        session.add(_target_observation(check, chat_id, target_kind, item=item))


def target_chat_ids(
    campaign: Campaign, targets: tuple[QualificationTarget, ...]
) -> tuple[int, ...]:
    if targets:
        return tuple(dict.fromkeys(int(target.chat_id) for target in targets))
    rules = CampaignRules.model_validate(campaign.rules)
    values = ([rules.required_channel_id] if rules.required_channel_id else []) + list(
        rules.required_chat_ids
    )
    return tuple(dict.fromkeys(values))


def kind_state(observations: tuple[dict[str, Any], ...], target_kind: str) -> QualificationState:
    states = {
        QualificationState(str(item["state"]))
        for item in observations
        if item.get("target_kind") == target_kind
    }
    if QualificationState.INELIGIBLE in states:
        return QualificationState.INELIGIBLE
    if QualificationState.UNKNOWN in states:
        return QualificationState.UNKNOWN
    return QualificationState.ELIGIBLE


async def mark_membership_ineligible(
    uow_factory: UnitOfWorkFactory,
    *,
    campaign_id: UUID,
    user_id: int,
    chat_id: int,
    observed_at: datetime,
) -> None:
    async with uow_factory() as uow:
        participant = await uow.session.scalar(
            select(Participant)
            .where(
                Participant.campaign_id == campaign_id,
                Participant.user_id == user_id,
            )
            .with_for_update()
        )
        if participant is None:
            return
        participant.qualification_state = QualificationState.INELIGIBLE
        uow.session.add(_membership_check(participant, chat_id, observed_at))


async def source_participation_group_id(
    session: object,
    *,
    campaign_id: UUID,
    source_chat_id: int | None,
) -> UUID | None:
    if source_chat_id is None:
        return None
    return await session.scalar(
        select(CampaignParticipationGroup.id)
        .join(Group, Group.id == CampaignParticipationGroup.group_id)
        .where(
            CampaignParticipationGroup.campaign_id == campaign_id,
            or_(
                CampaignParticipationGroup.chat_id_snapshot == source_chat_id,
                Group.chat_id == source_chat_id,
            ),
        )
    )


def accepts_registration(campaign: Campaign | None, observed_at: datetime) -> bool:
    return bool(
        campaign
        and campaign.state.value in _REGISTRATION_STATES
        and observed_at <= campaign.cutoff_at
    )


def can_apply_registration_result(campaign: Campaign, observed_at: datetime) -> bool:
    return campaign.state.value in _REGISTRATION_STATES and observed_at <= campaign.cutoff_at


def _target_observation(
    check: QualificationCheck,
    chat_id: int,
    target_kind: str,
    *,
    item: dict[str, Any],
) -> QualificationTargetObservation:
    request = {
        "check_id": str(check.id),
        "target_kind": target_kind,
        "chat_id": chat_id,
        "attempt_number": 1,
    }
    return QualificationTargetObservation(
        check_id=check.id,
        participant_id=check.participant_id,
        target_kind=target_kind,
        chat_id=chat_id,
        state=str(item["state"]),
        observed_status=str(item.get("observed_status") or "") or None,
        error_code=str(item.get("error_code") or "") or None,
        observed_at=datetime.fromisoformat(str(item["observed_at"])),
        attempt_number=1,
        request_hash=canonical_hash(request),
    )


def _membership_check(
    participant: Participant, chat_id: int, observed_at: datetime
) -> QualificationCheck:
    observation = {
        "chat_id": chat_id,
        "state": "ineligible",
        "observed_status": "left",
        "observed_at": observed_at.isoformat(),
    }
    return QualificationCheck(
        participant_id=participant.id,
        phase="membership_event",
        state=QualificationState.INELIGIBLE,
        observed_status="left",
        observations={"chats": [observation]},
        source_update_id=current_update_id(),
        observed_at=observed_at,
    )
