from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateContext:
    bot_id: int
    update_id: int


_CURRENT_UPDATE: ContextVar[UpdateContext | None] = ContextVar(
    "tg_lottery_current_update", default=None
)


@contextmanager
def bind_update_context(bot_id: int, update_id: int) -> Iterator[UpdateContext]:
    context = UpdateContext(bot_id=bot_id, update_id=update_id)
    token = _CURRENT_UPDATE.set(context)
    try:
        yield context
    finally:
        _CURRENT_UPDATE.reset(token)


def current_update() -> UpdateContext | None:
    return _CURRENT_UPDATE.get()


def current_update_id() -> int | None:
    context = current_update()
    return context.update_id if context else None
