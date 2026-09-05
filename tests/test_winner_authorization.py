from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from tests.test_winner_designations import _seed_case
from tg_lottery.application.winner_authorization import WinnerAuthorization
from tg_lottery.application.winner_designations import WinnerDesignationService
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import AuditLog, WinnerDesignation, WinnerDesignationOperator
from tg_lottery.infrastructure.db.uow import UnitOfWork
from tg_lottery.ops_access_config import OpsAccessSettings


def _service(session_factory, *, configured=frozenset()):
    return WinnerDesignationService(
        partial(UnitOfWork, session_factory), authorization=WinnerAuthorization(configured)
    )


async def _set(service, campaign_id, *, actor=101, reference="@chosen"):
    return await service.set(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        participant_reference=reference,
        actor_user_id=actor,
        source_bot_id=None,
    )


async def _remove(service, campaign_id, *, actor=101):
    return await service.remove(
        campaign_id=campaign_id,
        prize_position=1,
        prize_slot_no=1,
        actor_user_id=actor,
        source_bot_id=None,
    )


@pytest.mark.parametrize("actor", [101, 202, 999])
async def test_unlisted_creator_participant_and_stranger_cannot_call_service(
    session_factory, actor
):
    campaign_id, _, _ = await _seed_case(session_factory, datetime.now(UTC), winner_count=1)
    service = _service(session_factory)
    operations = (
        lambda: service.editable_campaigns(actor_user_id=actor),
        lambda: service.list(campaign_id, actor_user_id=actor),
        lambda: _set(service, campaign_id, actor=actor),
        lambda: _remove(service, campaign_id, actor=actor),
    )
    for operation in operations:
        with pytest.raises(UserFacingError, match="授权名单"):
            await operation()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(WinnerDesignation)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_database_grant_is_rechecked_after_revocation_and_audited(session_factory):
    campaign_id, first_id, chosen_id = await _seed_case(
        session_factory, datetime.now(UTC), winner_count=1
    )
    async with session_factory() as session, session.begin():
        session.add(WinnerDesignationOperator(user_id=101, note="test grant"))
    service = _service(session_factory)
    assert (await service.editable_campaigns(actor_user_id=101))[0].campaign_id == campaign_id
    await _set(service, campaign_id)
    await _set(service, campaign_id, reference="@first")
    async with session_factory() as session, session.begin():
        (await session.get(WinnerDesignationOperator, 101)).enabled = False
    with pytest.raises(UserFacingError, match="授权名单"):
        await _set(service, campaign_id)
    with pytest.raises(UserFacingError, match="授权名单"):
        await service.list(campaign_id, actor_user_id=101)
    with pytest.raises(UserFacingError, match="授权名单"):
        await _remove(service, campaign_id)
    async with session_factory() as session:
        assert (await session.scalar(select(WinnerDesignation))).participant_id == first_id
        audits = list(await session.scalars(select(AuditLog).order_by(AuditLog.created_at)))
    assert len(audits) == 2
    assert all(row.details["authorization_source"] == "database" for row in audits)
    assert audits[-1].details["before_participant_id"] == str(chosen_id)


async def test_database_grant_deletion_and_config_grant_are_independent(session_factory):
    campaign_id, _, _ = await _seed_case(session_factory, datetime.now(UTC), winner_count=1)
    async with session_factory() as session, session.begin():
        session.add(WinnerDesignationOperator(user_id=101))
    database_service = _service(session_factory)
    await _set(database_service, campaign_id)
    assert await _remove(database_service, campaign_id)
    async with session_factory() as session, session.begin():
        await session.delete(await session.get(WinnerDesignationOperator, 101))
    with pytest.raises(UserFacingError, match="授权名单"):
        await database_service.require_authorized(101)
    configured_service = _service(session_factory, configured=frozenset({101}))
    await _set(configured_service, campaign_id)
    assert await _remove(configured_service, campaign_id)
    async with session_factory() as session:
        audits = list(await session.scalars(select(AuditLog).order_by(AuditLog.created_at)))
    assert [row.details["authorization_source"] for row in audits] == [
        "database",
        "database",
        "config",
        "config",
    ]


@pytest.mark.parametrize("actor", [0, -101, True, "101"])
async def test_invalid_actor_cannot_query_campaign_existence(session_factory, actor):
    with pytest.raises(UserFacingError, match="授权名单"):
        await _service(session_factory).list(uuid4(), actor_user_id=actor)


@pytest.mark.parametrize("value", ["0", "-1", "101,no", "101,0"])
def test_invalid_config_grants_are_rejected(value):
    with pytest.raises(ValueError):
        OpsAccessSettings(ops_admin_ids=value, database_url="postgresql+asyncpg://test/test")


def test_config_file_is_a_supported_explicit_grant_source(tmp_path, monkeypatch):
    monkeypatch.delenv("OPS_ADMIN_IDS", raising=False)
    config = tmp_path / "operators.env"
    config.write_text("OPS_ADMIN_IDS=101,202\nDATABASE_URL=postgresql+asyncpg://test/test\n")
    settings = OpsAccessSettings(_env_file=config)
    assert settings.ops_admin_ids == frozenset({101, 202})


async def test_database_never_generates_an_operator_identity_when_id_is_omitted(session_factory):
    with pytest.raises(IntegrityError):
        async with session_factory() as session, session.begin():
            await session.execute(
                text("INSERT INTO winner_designation_operators (note) VALUES (:note)"),
                {"note": "missing explicit identity"},
            )
