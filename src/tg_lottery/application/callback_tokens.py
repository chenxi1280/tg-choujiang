import secrets
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select

from tg_lottery.application.ports import UnitOfWorkFactory
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import CallbackToken

_TOKEN_TTL_MINUTES = 10


@dataclass(frozen=True)
class CallbackGrant:
    action: str
    campaign_id: UUID | None
    payload: dict[str, object]


@dataclass(frozen=True)
class CallbackRequest:
    action: str
    campaign_id: UUID | None = None
    payload: dict[str, object] | None = None


class CallbackTokenService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def create_many(
        self,
        *,
        owner_user_id: int,
        chat_id: int,
        requests: tuple[CallbackRequest, ...],
    ) -> tuple[str, ...]:
        async with self._uow_factory() as uow:
            tokens: list[str] = []
            for request in requests:
                token = secrets.token_urlsafe(18)
                tokens.append(token)
                uow.session.add(
                    CallbackToken(
                        token_hash=_hash(token),
                        owner_user_id=owner_user_id,
                        chat_id=chat_id,
                        campaign_id=request.campaign_id,
                        action=request.action,
                        payload=request.payload or {},
                        expires_at=utcnow() + timedelta(minutes=_TOKEN_TTL_MINUTES),
                    )
                )
            return tuple(tokens)

    async def consume(
        self,
        token: str,
        *,
        owner_user_id: int,
        chat_id: int,
    ) -> CallbackGrant:
        async with self._uow_factory() as uow:
            row = await uow.session.scalar(
                select(CallbackToken)
                .where(CallbackToken.token_hash == _hash(token))
                .with_for_update()
            )
            if row is None:
                raise AuthorizationDenied("callback token does not exist")
            if row.owner_user_id != owner_user_id or row.chat_id != chat_id:
                raise AuthorizationDenied("callback token owner or chat does not match")
            if row.consumed_at is not None or row.expires_at <= utcnow():
                raise AuthorizationDenied("callback token is expired or already used")
            row.consumed_at = utcnow()
            return CallbackGrant(row.action, row.campaign_id, row.payload)


def _hash(token: str) -> bytes:
    return sha256(token.encode()).digest()
