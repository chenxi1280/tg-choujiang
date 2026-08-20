from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_audit, utcnow
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.infrastructure.db import Campaign, Participant, Prize, WinnerDesignation

_EDITABLE_STATES = frozenset(
    {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
        CampaignState.PAUSED,
        CampaignState.SETTLING,
        CampaignState.READY_TO_DRAW,
    }
)


@dataclass(frozen=True)
class WinnerDesignationView:
    prize_position: int
    prize_name: str
    prize_slot_no: int
    participant_user_id: int
    participant_username: str | None
    participant_display_name: str


class WinnerDesignationService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def list(self, campaign_id: UUID) -> tuple[WinnerDesignationView, ...]:
        async with self._uow_factory() as uow:
            rows = await uow.session.execute(
                select(WinnerDesignation, Prize, Participant)
                .join(Prize, Prize.id == WinnerDesignation.prize_id)
                .join(Participant, Participant.id == WinnerDesignation.participant_id)
                .where(WinnerDesignation.campaign_id == campaign_id)
                .order_by(Prize.position, WinnerDesignation.prize_slot_no)
            )
            return tuple(_view(*row) for row in rows)

    async def set(
        self,
        *,
        campaign_id: UUID,
        prize_position: int,
        prize_slot_no: int,
        participant_reference: str,
        actor_user_id: int,
    ) -> WinnerDesignationView:
        async with self._uow_factory() as uow:
            campaign = await _editable_campaign(uow.session, campaign_id)
            prize = await _prize(uow.session, campaign_id, prize_position)
            _validate_slot(prize, prize_slot_no)
            participant = await _participant(uow.session, campaign_id, participant_reference)
            designation = await _slot_designation(
                uow.session,
                campaign_id=campaign_id,
                prize_id=prize.id,
                prize_slot_no=prize_slot_no,
            )
            await _require_unique_participant(
                uow.session,
                campaign=campaign,
                participant_id=participant.id,
                current_id=designation.id if designation else None,
            )
            designation, before = await _assign_designation(
                uow.session,
                designation=designation,
                campaign_id=campaign_id,
                prize=prize,
                prize_slot_no=prize_slot_no,
                participant=participant,
                actor_user_id=actor_user_id,
            )
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="winner.designated",
                aggregate_type="winner_designation",
                aggregate_id=str(designation.id),
                details={
                    "campaign_id": str(campaign_id),
                    "prize_id": str(prize.id),
                    "prize_slot_no": prize_slot_no,
                    "before_participant_id": before,
                    "participant_id": str(participant.id),
                },
            )
            return _view(designation, prize, participant)

    async def remove(
        self,
        *,
        campaign_id: UUID,
        prize_position: int,
        prize_slot_no: int,
        actor_user_id: int,
    ) -> bool:
        async with self._uow_factory() as uow:
            await _editable_campaign(uow.session, campaign_id)
            prize = await _prize(uow.session, campaign_id, prize_position)
            _validate_slot(prize, prize_slot_no)
            designation = await _slot_designation(
                uow.session,
                campaign_id=campaign_id,
                prize_id=prize.id,
                prize_slot_no=prize_slot_no,
            )
            if designation is None:
                return False
            add_audit(
                uow.session,
                actor_user_id=actor_user_id,
                action="winner.designation_removed",
                aggregate_type="winner_designation",
                aggregate_id=str(designation.id),
                details={
                    "campaign_id": str(campaign_id),
                    "prize_id": str(prize.id),
                    "prize_slot_no": prize_slot_no,
                    "participant_id": str(designation.participant_id),
                },
            )
            await uow.session.delete(designation)
            return True


async def _editable_campaign(session: object, campaign_id: UUID) -> Campaign:
    campaign = await session.scalar(
        select(Campaign).where(Campaign.id == campaign_id).with_for_update()
    )
    if campaign is None:
        raise UserFacingError("活动不存在")
    if campaign.state not in _EDITABLE_STATES:
        raise UserFacingError("当前活动状态不能修改指定中奖人")
    return campaign


async def _prize(session: object, campaign_id: UUID, position: int) -> Prize:
    prize = await session.scalar(
        select(Prize).where(Prize.campaign_id == campaign_id, Prize.position == position)
    )
    if prize is None:
        raise UserFacingError("奖品顺位不存在")
    return prize


async def _participant(session: object, campaign_id: UUID, reference: str) -> Participant:
    value = reference.strip()
    if value.startswith("@") and len(value) > 1:
        statement = select(Participant).where(
            Participant.campaign_id == campaign_id,
            func.lower(Participant.username) == value[1:].lower(),
        )
    elif value.isdigit() and int(value) > 0:
        statement = select(Participant).where(
            Participant.campaign_id == campaign_id,
            Participant.user_id == int(value),
        )
    else:
        raise UserFacingError("参与者请输入 @用户名 或 Telegram 用户 ID")
    rows = tuple(await session.scalars(statement.limit(2)))
    if not rows:
        raise UserFacingError("该用户尚未报名本活动")
    if len(rows) > 1:
        raise UserFacingError("用户名匹配到多个报名记录, 请改用 Telegram 用户 ID")
    return rows[0]


async def _slot_designation(
    session: object,
    *,
    campaign_id: UUID,
    prize_id: UUID,
    prize_slot_no: int,
) -> WinnerDesignation | None:
    return await session.scalar(
        select(WinnerDesignation)
        .where(
            WinnerDesignation.campaign_id == campaign_id,
            WinnerDesignation.prize_id == prize_id,
            WinnerDesignation.prize_slot_no == prize_slot_no,
        )
        .with_for_update()
    )


async def _assign_designation(
    session: object,
    *,
    designation: WinnerDesignation | None,
    campaign_id: UUID,
    prize: Prize,
    prize_slot_no: int,
    participant: Participant,
    actor_user_id: int,
) -> tuple[WinnerDesignation, str | None]:
    before = str(designation.participant_id) if designation else None
    if designation is None:
        designation = WinnerDesignation(
            campaign_id=campaign_id,
            prize_id=prize.id,
            prize_slot_no=prize_slot_no,
            participant_id=participant.id,
            designated_by=actor_user_id,
            designated_at=utcnow(),
        )
        session.add(designation)
    else:
        designation.participant_id = participant.id
        designation.designated_by = actor_user_id
        designation.designated_at = utcnow()
    await session.flush()
    return designation, before


async def _require_unique_participant(
    session: object,
    *,
    campaign: Campaign,
    participant_id: UUID,
    current_id: UUID | None,
) -> None:
    if not CampaignRules.model_validate(campaign.rules).unique_winner:
        return
    statement = select(WinnerDesignation.id).where(
        WinnerDesignation.campaign_id == campaign.id,
        WinnerDesignation.participant_id == participant_id,
    )
    if current_id is not None:
        statement = statement.where(WinnerDesignation.id != current_id)
    if await session.scalar(statement) is not None:
        raise UserFacingError("当前活动要求中奖人唯一, 该用户已被指定到其他名额")


def _validate_slot(prize: Prize, prize_slot_no: int) -> None:
    if not 1 <= prize_slot_no <= prize.winner_count:
        raise UserFacingError(f"该奖品的名额应在 1-{prize.winner_count} 之间")


def _view(
    designation: WinnerDesignation, prize: Prize, participant: Participant
) -> WinnerDesignationView:
    return WinnerDesignationView(
        prize_position=prize.position,
        prize_name=prize.name,
        prize_slot_no=designation.prize_slot_no,
        participant_user_id=participant.user_id,
        participant_username=participant.username,
        participant_display_name=participant.display_name,
    )
