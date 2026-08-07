from enum import StrEnum


class CampaignState(StrEnum):
    DRAFT = "draft"
    PUBLISHING = "publishing"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    SETTLING = "settling"
    READY_TO_DRAW = "ready_to_draw"
    DRAWING = "drawing"
    DRAWN = "drawn"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class QualificationState(StrEnum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


class InvitationState(StrEnum):
    ATTRIBUTION_CANDIDATE = "attribution_candidate"
    JOINED_PENDING = "joined_pending"
    CHECKING = "checking"
    VALID = "valid"
    LEFT_BEFORE_VALID = "left_before_valid"
    INVALID = "invalid"
    VALIDATION_UNKNOWN = "validation_unknown"
    EXPIRED_UNQUALIFIED = "expired_unqualified"


class UpdateState(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class JobState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED_TERMINAL = "failed_terminal"


class OutboxState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_SCHEDULED = "retry_scheduled"
    CONFIRMED = "confirmed"
    PRE_ACCEPT_FAILED = "pre_accept_failed"
    UNKNOWN_HOLD = "unknown_hold"
    CANCELLED = "cancelled"


class DeliveryCertainty(StrEnum):
    CONFIRMED = "confirmed"
    EXPLICIT_REJECTION = "explicit_rejection"
    UNKNOWN = "unknown"


class TicketSource(StrEnum):
    POPULARITY = "popularity"
    INVITATION = "invitation"
    REVERSAL = "reversal"


class PrizeClaimState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    FORFEITED = "forfeited"
