from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class UnitOfWorkPort(Protocol):
    session: AsyncSession

    async def __aenter__(self) -> "UnitOfWorkPort": ...

    async def __aexit__(self, *args: object) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
