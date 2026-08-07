import secrets
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import add_outbox, utcnow
from tg_lottery.domain.draw import ALGORITHM_VERSION, WeightedEntry, select_weighted
from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.domain.transitions import require_campaign_transition
from tg_lottery.infrastructure.db import (
    Campaign,
    DrawEntry,
    DrawRound,
    DrawRun,
    DrawSnapshot,
    Group,
    Participant,
    Prize,
    Winner,
)


@dataclass(frozen=True)
class DrawCandidate:
    entry: DrawEntry
    participant: Participant


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
            existing = await uow.session.scalar(
                select(DrawRun).where(
                    DrawRun.snapshot_id == snapshot.id,
                    DrawRun.algorithm_version == ALGORITHM_VERSION,
                )
            )
            if existing:
                return existing
            require_campaign_transition(campaign.state, CampaignState.DRAWING)
            campaign.state = CampaignState.DRAWING
            draw_run = DrawRun(
                snapshot_id=snapshot.id,
                algorithm_version=ALGORITHM_VERSION,
                random_input=secrets.token_hex(32),
                started_by=actor_user_id,
            )
            uow.session.add(draw_run)
            await uow.session.flush()
            winners = await self._draw_all(
                uow.session,
                campaign=campaign,
                snapshot=snapshot,
                draw_run=draw_run,
            )
            await self._queue_notifications(
                uow.session,
                campaign=campaign,
                draw_run=draw_run,
                winners=winners,
            )
            draw_run.completed_at = utcnow()
            campaign.state = CampaignState.DRAWN
            return draw_run

    async def _draw_all(
        self,
        session: object,
        *,
        campaign: Campaign,
        snapshot: DrawSnapshot,
        draw_run: DrawRun,
    ) -> list[tuple[Winner, Participant, Prize]]:
        candidates = await self._candidates(session, snapshot.id)
        prizes = (
            await session.scalars(
                select(Prize)
                .where(Prize.campaign_id == campaign.id)
                .order_by(Prize.position, Prize.id)
            )
        ).all()
        rules = CampaignRules.model_validate(campaign.rules)
        winners: list[tuple[Winner, Participant, Prize]] = []
        round_number = 0
        for prize in prizes:
            for _ in range(prize.quantity):
                round_number += 1
                result = await self._draw_round(
                    session,
                    draw_run=draw_run,
                    prize=prize,
                    candidates=candidates,
                    round_number=round_number,
                )
                if result is None:
                    continue
                winner, participant = result
                winners.append((winner, participant, prize))
                if rules.unique_winner:
                    candidates = [
                        item for item in candidates if item.participant.id != participant.id
                    ]
        return winners

    async def _draw_round(
        self,
        session: object,
        *,
        draw_run: DrawRun,
        prize: Prize,
        candidates: list[DrawCandidate],
        round_number: int,
    ) -> tuple[Winner, Participant] | None:
        if not candidates:
            session.add(self._undistributed_round(draw_run, prize, round_number))
            return None
        weighted = tuple(
            WeightedEntry(str(candidate.participant.id), candidate.entry.weight)
            for candidate in candidates
        )
        selection = select_weighted(weighted, draw_run.random_input, round_number)
        participant = next(
            item.participant
            for item in candidates
            if str(item.participant.id) == selection.participant_id
        )
        draw_round = DrawRound(
            draw_run_id=draw_run.id,
            round_number=round_number,
            prize_id=prize.id,
            range_start=selection.range_start,
            range_end=selection.range_end,
            selected_value=selection.selected_value,
            selected_participant_id=participant.id,
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
        return winner, participant

    def _undistributed_round(self, draw_run: DrawRun, prize: Prize, round_number: int) -> DrawRound:
        return DrawRound(
            draw_run_id=draw_run.id,
            round_number=round_number,
            prize_id=prize.id,
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
        winners: list[tuple[Winner, Participant, Prize]],
    ) -> None:
        group = await session.get(Group, campaign.group_id)
        if group is None:
            raise ValueError("campaign group is missing")
        add_outbox(
            session,
            operation="send_draw_result",
            aggregate_type="draw_run",
            aggregate_id=str(draw_run.id),
            recipient_chat_id=group.chat_id,
            business_key=f"draw_result_group:{draw_run.id}:{group.chat_id}",
            payload={
                "campaign_name": campaign.name,
                "snapshot_hash": (await self._snapshot(session, campaign.id)).snapshot_hash,
                "winners": [self._winner_payload(item) for item in winners],
            },
        )
        for item in winners:
            winner, participant, prize = item
            add_outbox(
                session,
                operation="send_winner_notice",
                aggregate_type="winner",
                aggregate_id=str(winner.id),
                recipient_chat_id=participant.user_id,
                business_key=f"winner_notice:{winner.id}:{participant.user_id}",
                payload={"campaign_name": campaign.name, "prize_name": prize.name},
            )

    def _winner_payload(self, item: tuple[Winner, Participant, Prize]) -> dict[str, object]:
        _, participant, prize = item
        return {
            "user_id": participant.user_id,
            "display_name": participant.display_name,
            "prize_name": prize.name,
        }
