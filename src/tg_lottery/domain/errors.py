class DomainError(Exception):
    """Expected business-rule violation."""


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
