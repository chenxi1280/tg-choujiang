from dataclasses import dataclass
from uuid import UUID

from tg_lottery.domain.rules import canonical_hash
from tg_lottery.infrastructure.db import Prize


@dataclass(frozen=True)
class FrozenPrize:
    id: UUID
    position: int
    name: str
    winner_count: int
    claim_instructions: str


def freeze_prizes(prizes: tuple[Prize, ...] | list[Prize]) -> tuple[FrozenPrize, ...]:
    return tuple(
        FrozenPrize(
            id=prize.id,
            position=prize.position,
            name=prize.name,
            winner_count=prize.winner_count,
            claim_instructions=prize.claim_instructions,
        )
        for prize in prizes
    )


def prize_plan_hash(prizes: tuple[Prize, ...] | list[Prize]) -> str:
    payload = [
        {
            "id": str(prize.id),
            "position": prize.position,
            "name": prize.name,
            "winner_count": prize.winner_count,
            "claim_instructions": prize.claim_instructions,
        }
        for prize in freeze_prizes(prizes)
    ]
    return canonical_hash({"prizes": payload})
