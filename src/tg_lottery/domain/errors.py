from datetime import datetime


class DomainError(Exception):
    """Expected business-rule violation."""


class UserFacingError(DomainError):
    """Expected validation failure with an explicitly reviewed public message."""

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class RegionLockedError(DomainError):
    def __init__(self, locked_until: datetime) -> None:
        super().__init__("region change is locked")
        self.locked_until = locked_until


class InvalidStateTransition(DomainError):
    pass


class AuthorizationDenied(DomainError):
    pass


class SettlementBlocked(DomainError):
    pass


class DeliveryUnknown(DomainError):
    pass


class InvitationValidationDeferred(DomainError):
    pass
