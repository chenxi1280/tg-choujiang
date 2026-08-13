from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules

ATTRIBUTION_STATES = frozenset(
    {
        CampaignState.SCHEDULED,
        CampaignState.RUNNING,
        CampaignState.PAUSED,
        CampaignState.SETTLING,
    }
)


@dataclass(frozen=True)
class JoinObservation:
    invite_link_id: UUID
    invitee_user_id: int
    event_at: datetime
    is_bot: bool = False
    was_already_member: bool = False


@dataclass(frozen=True)
class InvitationValidationAttempt:
    invitation_id: UUID
    invitee_user_id: int
    chat_ids: tuple[int, ...]
    requires_observation: bool


def required_chat_ids(main_chat_id: int, rules: CampaignRules) -> tuple[int, ...]:
    values = (main_chat_id, rules.required_channel_id, *rules.required_chat_ids)
    return tuple(dict.fromkeys(int(value) for value in values if value is not None))


def accepts_attribution(
    state: CampaignState,
    *,
    preheat_at: datetime,
    cutoff_at: datetime,
    event_at: datetime,
) -> bool:
    return state in ATTRIBUTION_STATES and preheat_at <= event_at <= cutoff_at
