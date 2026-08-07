from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import InvalidStateTransition

_ALLOWED: dict[CampaignState, frozenset[CampaignState]] = {
    CampaignState.DRAFT: frozenset({CampaignState.PUBLISHING, CampaignState.CANCELLED}),
    CampaignState.PUBLISHING: frozenset(
        {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.CANCELLED}
    ),
    CampaignState.SCHEDULED: frozenset(
        {
            CampaignState.RUNNING,
            CampaignState.PAUSED,
            CampaignState.SETTLING,
            CampaignState.CANCELLED,
        }
    ),
    CampaignState.RUNNING: frozenset(
        {CampaignState.PAUSED, CampaignState.SETTLING, CampaignState.CANCELLED}
    ),
    CampaignState.PAUSED: frozenset(
        {CampaignState.RUNNING, CampaignState.SETTLING, CampaignState.CANCELLED}
    ),
    CampaignState.SETTLING: frozenset({CampaignState.READY_TO_DRAW, CampaignState.CANCELLED}),
    CampaignState.READY_TO_DRAW: frozenset({CampaignState.DRAWING, CampaignState.CANCELLED}),
    CampaignState.DRAWING: frozenset({CampaignState.DRAWN}),
    CampaignState.DRAWN: frozenset({CampaignState.ARCHIVED}),
    CampaignState.ARCHIVED: frozenset(),
    CampaignState.CANCELLED: frozenset({CampaignState.ARCHIVED}),
}


def require_campaign_transition(current: CampaignState, target: CampaignState) -> None:
    if target not in _ALLOWED[current]:
        raise InvalidStateTransition(f"campaign transition {current} -> {target} is not allowed")
