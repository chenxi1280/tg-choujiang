from functools import partial

import pytest
from sqlalchemy import select

from tg_lottery.application.admin_sessions import AdminSessionService
from tg_lottery.application.callback_tokens import CallbackRequest, CallbackTokenService
from tg_lottery.application.request_context import bind_update_context
from tg_lottery.domain.errors import AuthorizationDenied
from tg_lottery.infrastructure.db import CallbackToken
from tg_lottery.infrastructure.db.uow import UnitOfWork


async def test_current_draft_panel_callback_is_accepted(session_factory) -> None:
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    tokens = CallbackTokenService(partial(UnitOfWork, session_factory))
    await sessions.set(
        201,
        "draft:idle",
        {"draft_id": "draft-a", "draft_revision": 2, "panel_message_id": 301},
    )
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(
            CallbackRequest(
                "draft_publish",
                payload={
                    "draft_id": "draft-a",
                    "draft_revision": 2,
                    "panel_message_id": 301,
                },
            ),
        ),
    )

    grant = await tokens.consume(token, owner_user_id=201, chat_id=201)

    assert grant.action == "draft_publish"


async def test_old_draft_panel_is_rejected_without_consuming_token(session_factory) -> None:
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    tokens = CallbackTokenService(partial(UnitOfWork, session_factory))
    await sessions.set(
        201,
        "draft:idle",
        {"draft_id": "draft-a", "draft_revision": 2, "panel_message_id": 301},
    )
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(
            CallbackRequest(
                "draft_publish",
                payload={
                    "draft_id": "draft-a",
                    "draft_revision": 1,
                    "panel_message_id": 300,
                },
            ),
        ),
    )

    with pytest.raises(AuthorizationDenied, match="old panel"):
        await tokens.consume(token, owner_user_id=201, chat_id=201)

    async with session_factory() as session:
        stored = await session.scalar(select(CallbackToken))
    assert stored is not None and stored.consumed_at is None


async def test_legacy_unscoped_draft_callback_is_rejected(session_factory) -> None:
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    tokens = CallbackTokenService(partial(UnitOfWork, session_factory))
    await sessions.set(
        201,
        "draft:idle",
        {"draft_id": "draft-a", "draft_revision": 1, "panel_message_id": 301},
    )
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(CallbackRequest("draft_publish"),),
    )

    with pytest.raises(AuthorizationDenied, match="old panel"):
        await tokens.consume(token, owner_user_id=201, chat_id=201)


async def test_same_update_replays_draft_callback_after_session_is_cleared(
    session_factory,
) -> None:
    sessions = AdminSessionService(partial(UnitOfWork, session_factory))
    tokens = CallbackTokenService(partial(UnitOfWork, session_factory))
    await sessions.set(
        201,
        "draft:idle",
        {"draft_id": "draft-a", "draft_revision": 2, "panel_message_id": 301},
    )
    request = CallbackRequest(
        "draft_publish",
        payload={
            "draft_id": "draft-a",
            "draft_revision": 2,
            "panel_message_id": 301,
        },
    )
    (token,) = await tokens.create_many(
        owner_user_id=201,
        chat_id=201,
        requests=(request,),
    )

    with bind_update_context(123, 88):
        first = await tokens.consume(token, owner_user_id=201, chat_id=201)
        await sessions.clear(201)
        replay = await tokens.consume(token, owner_user_id=201, chat_id=201)

    assert replay == first
