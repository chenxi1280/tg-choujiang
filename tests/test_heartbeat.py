import asyncio

import pytest

from tg_lottery.infrastructure.heartbeat import run_with_heartbeat

HEARTBEAT_INTERVAL_SECONDS = 0.001
SLOW_OPERATION_SECONDS = 1


class ExplicitOperationError(Exception):
    pass


class HeartbeatError(Exception):
    pass


async def test_run_with_heartbeat_preserves_operation_exception_type() -> None:
    async def operation() -> None:
        raise ExplicitOperationError("rejected")

    async def heartbeat() -> None:
        return None

    with pytest.raises(ExplicitOperationError, match="rejected"):
        await run_with_heartbeat(operation(), heartbeat, HEARTBEAT_INTERVAL_SECONDS)


async def test_run_with_heartbeat_surfaces_heartbeat_failure() -> None:
    async def operation() -> None:
        await asyncio.sleep(SLOW_OPERATION_SECONDS)

    async def heartbeat() -> None:
        raise HeartbeatError("lost lease")

    with pytest.raises(HeartbeatError, match="lost lease"):
        await run_with_heartbeat(operation(), heartbeat, HEARTBEAT_INTERVAL_SECONDS)
