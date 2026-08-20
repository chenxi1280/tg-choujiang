from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.campaign_notifications import add_campaign_card_state_fanout
from tg_lottery.application.campaign_targets import (
    participation_chat_ids,
    release_participation_groups,
)
from tg_lottery.application.draw_commitments import draw_seed
from tg_lottery.application.operational_blocks import has_active_campaign_block
from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.prize_plan import prize_plan_hash
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.draw import ALGORITHM_VERSION, Selection, WeightedEntry, select_weighted
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawEntry,
    DrawRound,
    DrawRun,
    DrawSnapshot,
    Participant,
    Prize,
    Winner,
    WinnerDesignation,
)


@dataclass(frozen=True)
class DrawCandidate:
    entry: DrawEntry
    participant: Participant


@dataclass(frozen=True)
class Award:
    winner: Winner
    participant: Participant
    prize: Prize
    prize_slot_no: int
    selection_mode: str


@dataclass(frozen=True)
class Undistributed:
    prize: Prize
    count: int


@dataclass(frozen=True)
class DrawOutcome:
    awards: tuple[Award, ...]
    undistributed: tuple[Undistributed, ...]


class DrawingService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def run(self, campaign_id: UUID, actor_user_id: int | None = None) -> DrawRun:
        async with self._uow_factory() as uow:
            campaign = await uow.session.scalar(
                select(Campaign).where(Campaign.id == campaign_id).with_for_update()
            )
            if campaign is None:
                raise ValueError("campaign not found")
            snapshot = await self._snapshot(uow.session, campaign.id)
            prizes = await self._prizes(uow.session, campaign.id)
            self._validate_prize_plan(campaign, snapshot, prizes)
            existing = await uow.session.scalar(
                select(DrawRun).where(
                    DrawRun.snapshot_id == snapshot.id,
                    DrawRun.algorithm_version == ALGORITHM_VERSION,
                )
            )
            if existing:
                return existing
            if await has_active_campaign_block(uow.session, campaign.id):
                raise UserFacingError("活动存在群权限阻断, 恢复并重新核验后才能开奖")
            require_campaign_transition(campaign.state, CampaignState.DRAWING)
            campaign.state = CampaignState.DRAWING
            now = utcnow()
            draw_run = DrawRun(
                snapshot_id=snapshot.id,
                algorithm_version=ALGORITHM_VERSION,
                random_input=await draw_seed(uow.session, campaign, now),
                started_by=actor_user_id,
            )
            uow.session.add(draw_run)
            await uow.session.flush()
            outcome = await self._draw_all(
                uow.session,
                snapshot=snapshot,
                draw_run=draw_run,
                prizes=prizes,
                unique_winner=CampaignRules.model_validate(campaign.rules).unique_winner,
            )
            await self._queue_notifications(
                uow.session,
                campaign=campaign,
                draw_run=draw_run,
                outcome=outcome,
            )
            draw_run.completed_at = now
            campaign.state = CampaignState.DRAWN
            await release_participation_groups(uow.session, campaign, utcnow())
            return draw_run

    async def _draw_all(
        self,
        session: object,
        *,
        snapshot: DrawSnapshot,
        draw_run: DrawRun,
        prizes: tuple[Prize, ...],
        unique_winner: bool,
    ) -> DrawOutcome:
        candidates = await self._candidates(session, snapshot.id)
        designations = await self._designations(session, snapshot.campaign_id, prizes)
        awards: list[Award] = []
        undistributed: list[Undistributed] = []
        round_number = 0
        for prize in prizes:
            missing = 0
            for prize_slot_no in range(1, prize.winner_count + 1):
                round_number += 1
                designation = designations.pop((prize.id, prize_slot_no), None)
                round_candidates = _available_candidates(
                    candidates,
                    designations=designations,
                    reserve_designated=unique_winner and designation is None,
                )
                result = await self._draw_round(
                    session,
                    draw_run=draw_run,
                    prize=prize,
                    candidates=round_candidates,
                    round_number=round_number,
                    prize_slot_no=prize_slot_no,
                    designation=designation,
                )
                if result is None:
                    missing += 1
                    continue
                winner, participant = result
                mode = "designated" if designation else "random"
                awards.append(Award(winner, participant, prize, prize_slot_no, mode))
                if unique_winner:
                    candidates = [
                        item for item in candidates if item.participant.id != participant.id
                    ]
            if missing:
                undistributed.append(Undistributed(prize, missing))
        return DrawOutcome(tuple(awards), tuple(undistributed))

    async def _draw_round(
        self,
        session: object,
        *,
        draw_run: DrawRun,
        prize: Prize,
        candidates: list[DrawCandidate],
        round_number: int,
        prize_slot_no: int,
        designation: WinnerDesignation | None,
    ) -> tuple[Winner, Participant] | None:
        if designation is not None:
            participant = _designated_participant(candidates, designation)
            winner = await self._record_winner(
                session,
                draw_run=draw_run,
                prize=prize,
                participant=participant,
                round_number=round_number,
                prize_slot_no=prize_slot_no,
                selection=None,
                designation=designation,
            )
            return winner, participant
        if not candidates:
            session.add(
                self._undistributed_round(
                    draw_run,
                    prize,
                    round_number=round_number,
                    prize_slot_no=prize_slot_no,
                )
            )
            return None
        selection, participant = _random_selection(candidates, draw_run.random_input, round_number)
        winner = await self._record_winner(
            session,
            draw_run=draw_run,
            prize=prize,
            participant=participant,
            round_number=round_number,
            prize_slot_no=prize_slot_no,
            selection=selection,
            designation=None,
        )
        return winner, participant

    async def _record_winner(
        self,
        session: object,
        *,
        draw_run: DrawRun,
        prize: Prize,
        participant: Participant,
        round_number: int,
        prize_slot_no: int,
        selection: Selection | None,
        designation: WinnerDesignation | None,
    ) -> Winner:
        range_start, range_end, selected_value = _selection_values(selection)
        draw_round = DrawRound(
            draw_run_id=draw_run.id,
            round_number=round_number,
            prize_id=prize.id,
            prize_slot_no=prize_slot_no,
            range_start=range_start,
            range_end=range_end,
            selected_value=selected_value,
            selected_participant_id=participant.id,
            selection_mode="designated" if designation else "random",
            designation_id=designation.id if designation else None,
            undistributed=False,
        )
        session.add(draw_round)
        await session.flush()
        winner = Winner(
            draw_run_id=draw_run.id,
            draw_round_id=draw_round.id,
            prize_id=prize.id,
            participant_id=participant.id,
        )
        session.add(winner)
        await session.flush()
        return winner

    def _undistributed_round(
        self,
        draw_run: DrawRun,
        prize: Prize,
        *,
        round_number: int,
        prize_slot_no: int,
    ) -> DrawRound:
        return DrawRound(
            draw_run_id=draw_run.id,
            round_number=round_number,
            prize_id=prize.id,
            prize_slot_no=prize_slot_no,
            range_start=0,
            range_end=0,
            undistributed=True,
        )

    async def _candidates(self, session: object, snapshot_id: UUID) -> list[DrawCandidate]:
        rows = (
            await session.execute(
                select(DrawEntry, Participant)
                .join(Participant, Participant.id == DrawEntry.participant_id)
                .where(DrawEntry.snapshot_id == snapshot_id)
                .order_by(DrawEntry.user_id)
            )
        ).all()
        return [DrawCandidate(entry, participant) for entry, participant in rows]

    async def _prizes(self, session: object, campaign_id: UUID) -> tuple[Prize, ...]:
        rows = await session.scalars(
            select(Prize).where(Prize.campaign_id == campaign_id).order_by(Prize.position, Prize.id)
        )
        return tuple(rows)

    async def _designations(
        self,
        session: object,
        campaign_id: UUID,
        prizes: tuple[Prize, ...],
    ) -> dict[tuple[UUID, int], WinnerDesignation]:
        rows = tuple(
            await session.scalars(
                select(WinnerDesignation)
                .where(WinnerDesignation.campaign_id == campaign_id)
                .order_by(WinnerDesignation.prize_id, WinnerDesignation.prize_slot_no)
            )
        )
        slots = {(prize.id, slot) for prize in prizes for slot in range(1, prize.winner_count + 1)}
        if any((row.prize_id, row.prize_slot_no) not in slots for row in rows):
            raise UserFacingError("指定中奖记录与当前奖品名额不一致")
        return {(row.prize_id, row.prize_slot_no): row for row in rows}

    def _validate_prize_plan(
        self,
        campaign: Campaign,
        snapshot: DrawSnapshot,
        prizes: tuple[Prize, ...],
    ) -> None:
        frozen_hash = campaign.prize_plan_hash
        if frozen_hash is None or snapshot.prize_plan_hash is None:
            raise ValueError("campaign prize plan is not frozen")
        current_hash = prize_plan_hash(prizes)
        if current_hash != frozen_hash or snapshot.prize_plan_hash != frozen_hash:
            raise ValueError("campaign prize plan changed after publication")

    async def _snapshot(self, session: object, campaign_id: UUID) -> DrawSnapshot:
        snapshot = await session.scalar(
            select(DrawSnapshot).where(DrawSnapshot.campaign_id == campaign_id)
        )
        if snapshot is None:
            raise ValueError("campaign has no immutable draw snapshot")
        return snapshot

    async def _queue_notifications(
        self,
        session: object,
        *,
        campaign: Campaign,
        draw_run: DrawRun,
        outcome: DrawOutcome,
    ) -> None:
        winners = [self._winner_payload(item) for item in outcome.awards]
        await add_campaign_card_state_fanout(
            session,
            campaign,
            state="drawn",
            winners=winners,
        )
        presentation = campaign.presentation or {}
        extended_tips = bool(presentation.get("message_tips", True))
        group_extended_tips = extended_tips and all(
            item.selection_mode == "random" for item in outcome.awards
        )
        chat_ids = await participation_chat_ids(session, campaign.id, confirmed_only=True)
        for chat_id in chat_ids:
            await self._queue_group_result(
                session,
                campaign=campaign,
                draw_run=draw_run,
                outcome=outcome,
                chat_id=chat_id,
                extended_tips=group_extended_tips,
            )
        self._queue_winner_notices(
            session,
            campaign=campaign,
            outcome=outcome,
            presentation=presentation,
            extended_tips=extended_tips,
        )

    async def _queue_group_result(
        self,
        session: object,
        *,
        campaign: Campaign,
        draw_run: DrawRun,
        outcome: DrawOutcome,
        chat_id: int,
        extended_tips: bool,
    ) -> None:
        payload = {
            "campaign_name": campaign.name,
            "winners": [self._winner_payload(item) for item in outcome.awards],
            "undistributed": [
                {
                    "prize_position": item.prize.position,
                    "prize_name": item.prize.name,
                    "count": item.count,
                }
                for item in outcome.undistributed
            ],
            "extended_tips": extended_tips,
        }
        add_outbox(
            session,
            operation="send_draw_result",
            aggregate_type="draw_run",
            aggregate_id=str(draw_run.id),
            recipient_chat_id=chat_id,
            business_key=f"draw_result_group:{draw_run.id}:{chat_id}",
            payload=payload,
        )

    def _queue_winner_notices(
        self,
        session: object,
        *,
        campaign: Campaign,
        outcome: DrawOutcome,
        presentation: dict[str, object],
        extended_tips: bool,
    ) -> None:
        for item in outcome.awards:
            add_outbox(
                session,
                operation="send_winner_notice",
                aggregate_type="winner",
                aggregate_id=str(item.winner.id),
                recipient_chat_id=item.participant.user_id,
                business_key=f"winner_notice:{item.winner.id}:{item.participant.user_id}",
                payload={
                    "campaign_name": campaign.name,
                    "prize_name": item.prize.name,
                    "prize_slot_no": item.prize_slot_no,
                    "claim_instructions": item.prize.claim_instructions,
                    "extended_tips": extended_tips,
                    "issuer_name": presentation.get("issuer_name") or "活动创建者",
                },
            )

    def _winner_payload(self, item: Award) -> dict[str, object]:
        return {
            "user_id": item.participant.user_id,
            "display_name": item.participant.display_name,
            "prize_position": item.prize.position,
            "prize_name": item.prize.name,
            "prize_slot_no": item.prize_slot_no,
        }


def _available_candidates(
    candidates: list[DrawCandidate],
    *,
    designations: dict[tuple[UUID, int], WinnerDesignation],
    reserve_designated: bool,
) -> list[DrawCandidate]:
    if not reserve_designated:
        return candidates
    reserved = {row.participant_id for row in designations.values()}
    return [row for row in candidates if row.participant.id not in reserved]


def _random_selection(
    candidates: list[DrawCandidate], random_input: str, round_number: int
) -> tuple[Selection, Participant]:
    weighted = tuple(
        WeightedEntry(str(candidate.participant.id), candidate.entry.weight)
        for candidate in candidates
    )
    selection = select_weighted(weighted, random_input, round_number)
    participant = next(
        item.participant
        for item in candidates
        if str(item.participant.id) == selection.participant_id
    )
    return selection, participant


def _designated_participant(
    candidates: list[DrawCandidate], designation: WinnerDesignation
) -> Participant:
    participant = next(
        (row.participant for row in candidates if row.participant.id == designation.participant_id),
        None,
    )
    if participant is None:
        raise UserFacingError("指定中奖人未通过最终资格复核, 请重新指定或删除该名额")
    return participant


def _selection_values(selection: Selection | None) -> tuple[int, int, int | None]:
    if selection is None:
        return 0, 0, None
    return selection.range_start, selection.range_end, selection.selected_value
