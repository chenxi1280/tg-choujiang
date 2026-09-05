import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from tests.test_winner_designations import _seed_case
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.infrastructure.db import (
    AuditLog,
    Campaign,
    WinnerDesignation,
    WinnerDesignationOperator,
)
from tg_lottery.ops.winner_console import execute


async def test_console_rejects_unlisted_actor(session_factory, monkeypatch) -> None:
    monkeypatch.setenv("OPS_ADMIN_IDS", "101")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    with pytest.raises(UserFacingError, match="中奖指定授权名单"):
        await execute(actor_user_id=999, command="/campaigns")


async def test_console_sets_and_removes_with_named_audit_source(session_factory, monkeypatch):
    campaign_id, _, chosen_id = await _seed_case(session_factory, datetime.now(UTC), winner_count=1)
    monkeypatch.setenv("OPS_ADMIN_IDS", "101")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    async with session_factory() as session:
        reference = (await session.get(Campaign, campaign_id)).public_ref
    result = await execute(actor_user_id=101, command=f"/winner_set {reference} 1 1 @chosen")
    assert "指定成员" in result
    async with session_factory() as session:
        designation = await session.scalar(select(WinnerDesignation))
        audit = await session.scalar(select(AuditLog).where(AuditLog.action == "winner.designated"))
        assert designation.participant_id == chosen_id
        assert audit.details["source"] == "offline_ops_cli"
        assert audit.details["source_bot_id"] is None
    await execute(actor_user_id=101, command=f"/winner_del {reference} 1 1")
    async with session_factory() as session:
        assert await session.scalar(select(WinnerDesignation)) is None


async def test_console_accepts_database_only_grant_and_denies_after_disable(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OPS_ADMIN_IDS", "")
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    async with session_factory() as session, session.begin():
        session.add(WinnerDesignationOperator(user_id=202))
    assert "隔离开奖运维入口" in await execute(actor_user_id=202, command="/help")
    async with session_factory() as session, session.begin():
        (await session.get(WinnerDesignationOperator, 202)).enabled = False
    with pytest.raises(UserFacingError, match="授权名单"):
        await execute(actor_user_id=202, command="/help")
