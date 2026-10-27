import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pydantic import ValidationError

from tg_lottery.application.integration_snapshot import SnapshotRequest
from tg_lottery.integration_api import create_app
from tg_lottery.integration_config import IntegrationSettings


def test_settings_require_explicit_creator_scope():
    with pytest.raises(ValidationError):
        IntegrationSettings(token="x" * 32, creator_ids=[])
    with pytest.raises(ValidationError):
        IntegrationSettings(token="short", creator_ids=[1])


@pytest.mark.parametrize(
    "scope",
    [
        {"user_ids": [1, 1], "date_from": "2026-10-01", "date_to": "2026-10-01"},
        {"user_ids": [1], "date_from": "2026-10-01", "date_to": "2026-12-01"},
        {"user_ids": [2**63], "date_from": "2026-10-01", "date_to": "2026-10-01"},
    ],
)
def test_request_rejects_unbounded_or_ambiguous_scope(scope):
    with pytest.raises(ValidationError):
        SnapshotRequest.model_validate(scope)


async def test_authentication_and_validation_run_before_database_access():
    def unavailable_factory():
        raise AssertionError("unauthorized request reached database")

    settings = IntegrationSettings(token="x" * 32, creator_ids=[1])
    app = create_app(unavailable_factory, settings, bot_id=123)
    async with TestClient(TestServer(app)) as client:
        path = "/v1/campaigns/abcdefghijklmnopqrstuv/snapshot"
        response = await client.post(path, json={})
        assert response.status == 401
        response = await client.post(path, json={}, headers={"Authorization": "Bearer " + "x" * 32})
        assert response.status == 400
        response = await client.get(path)
        assert response.status == 405


async def test_valid_read_uses_readonly_transaction_and_owner_scope():
    statements = []

    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session(Transaction):
        def begin(self):
            return Transaction()

        async def execute(self, statement):
            statements.append(str(statement))

    async def snapshot(session, reference, request, *, creator_ids, bot_id):
        assert creator_ids == frozenset({7}) and bot_id == 123
        assert request.user_ids == [9999999999]
        return {"campaign_ref": reference, "registered": False}

    settings = IntegrationSettings(token="x" * 32, creator_ids=[7])
    app = create_app(Session, settings, bot_id=123, snapshot=snapshot)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/v1/campaigns/abcdefghijklmnopqrstuv/snapshot",
            headers={"Authorization": "Bearer " + "x" * 32},
            json={"user_ids": [9999999999], "date_from": "2026-10-27", "date_to": "2026-10-27"},
        )
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-store"
    assert "READ ONLY" in statements[0]
    assert "statement_timeout" in statements[1]


async def test_read_service_limits_concurrent_database_queries():
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def begin(self):
            return self

        async def execute(self, _statement):
            pass

    entered = []
    release = asyncio.Event()

    async def snapshot(*args, **kwargs):
        entered.append(True)
        await release.wait()
        return {"state": "read"}

    settings = IntegrationSettings(token="x" * 32, creator_ids=[7])
    app = create_app(Transaction, settings, bot_id=123, snapshot=snapshot)
    async with TestClient(TestServer(app)) as client:
        path = "/v1/campaigns/abcdefghijklmnopqrstuv/snapshot"
        request = {"user_ids": [123], "date_from": "2026-10-27", "date_to": "2026-10-27"}
        tasks = [
            asyncio.create_task(
                client.post(path, json=request, headers={"Authorization": "Bearer " + "x" * 32})
            )
            for _ in range(3)
        ]
        for _ in range(100):
            if len(entered) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(entered) == 2
        release.set()
        responses = await asyncio.gather(*tasks)
        assert all(response.status == 200 for response in responses)
        assert len(entered) == 3


def test_digest_scope_does_not_invent_unregistered_user_id():
    import hashlib

    from tg_lottery.application.integration_snapshot import _scoped_rows

    known = hashlib.sha256(b"123").hexdigest()
    unknown = hashlib.sha256(b"456").hexdigest()
    request = SnapshotRequest(
        user_digests=[known, unknown], date_from="2026-10-27", date_to="2026-10-27"
    )
    rows = _scoped_rows({123: {"user_id": "123", "registered": True}}, request)
    assert rows[0] == {"user_id": "123", "registered": True, "user_digest": known}
    assert rows[1] == {"registered": False, "user_digest": unknown}
    with pytest.raises(ValidationError):
        SnapshotRequest(
            user_ids=[123], user_digests=[known], date_from="2026-10-27", date_to="2026-10-27"
        )
