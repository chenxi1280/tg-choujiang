import asyncio

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiohttp import ClientConnectorError, web
from aiohttp.client import IDEMPOTENT_METHODS
from aiohttp.test_utils import TestServer

import tg_lottery.telegram.single_request_session as session_module
from tg_lottery.telegram.delivery_failures import failed_before_single_request
from tg_lottery.telegram.single_request_session import (
    PreRequestConnectionError,
    SingleRequestSession,
)

TEST_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"


def _api(server):
    return TelegramAPIServer.from_base(str(server.make_url("/")))


async def test_default_client_can_fail_to_connect_after_the_first_request():
    requests = []

    async def redirect(request):
        requests.append(request.method)
        return web.Response(status=307, headers={"Location": "http://127.0.0.1:1/"})

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", redirect)
    async with TestServer(app) as server:
        bot = Bot(TEST_TOKEN, session=AiohttpSession(api=_api(server)))
        try:
            with pytest.raises(TelegramNetworkError) as raised:
                await bot.send_message(chat_id=1, text="test")
            assert isinstance(raised.value.__cause__, ClientConnectorError)
            assert not failed_before_single_request("send_progress_notice", raised.value)
            assert requests == ["POST"]
        finally:
            await bot.session.close()


@pytest.mark.parametrize("status", [307, 500])
async def test_unconfirmed_http_responses_never_become_rate_limit_retries(status):
    requests = []

    async def redirect(request):
        requests.append(request.method)
        return web.json_response(
            {"ok": False, "description": "unexpected", "parameters": {"retry_after": 7}},
            status=status,
            headers={"Location": "http://127.0.0.1:1/"},
        )

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", redirect)
    async with TestServer(app) as server:
        bot = Bot(TEST_TOKEN, session=SingleRequestSession(api=_api(server)))
        try:
            with pytest.raises(TelegramNetworkError) as raised:
                await bot.send_message(chat_id=1, text="test")
            assert not isinstance(raised.value, PreRequestConnectionError)
            assert not isinstance(raised.value, TelegramRetryAfter)
            assert requests == ["POST"]
        finally:
            await bot.session.close()


async def test_single_request_session_proves_a_direct_connection_failure(monkeypatch):
    monkeypatch.setattr(session_module, "_verified_versions", lambda: True)
    server = TestServer(web.Application())
    await server.start_server()
    api = _api(server)
    await server.close()
    bot = Bot(TEST_TOKEN, session=SingleRequestSession(api=api))
    try:
        with pytest.raises(PreRequestConnectionError) as raised:
            await bot.send_message(chat_id=1, text="test")
        assert isinstance(raised.value.__cause__, ClientConnectorError)
        assert failed_before_single_request("send_progress_notice", raised.value)
    finally:
        await bot.session.close()


def test_verified_aiohttp_does_not_auto_retry_post_requests():
    if not session_module._verified_versions():
        pytest.skip("runtime version intentionally not eligible for pre-request proof")
    assert "POST" not in IDEMPOTENT_METHODS


async def test_unverified_dependency_version_keeps_connection_failure_unknown(monkeypatch):
    monkeypatch.setattr(session_module, "_verified_versions", lambda: False)
    server = TestServer(web.Application())
    await server.start_server()
    api = _api(server)
    await server.close()
    bot = Bot(TEST_TOKEN, session=SingleRequestSession(api=api))
    try:
        with pytest.raises(TelegramNetworkError) as raised:
            await bot.send_message(chat_id=1, text="test")
        assert not isinstance(raised.value, PreRequestConnectionError)
        assert not failed_before_single_request("send_progress_notice", raised.value)
    finally:
        await bot.session.close()


@pytest.mark.parametrize("rate_limited", [False, True])
async def test_single_request_session_preserves_normal_aiogram_responses(rate_limited):
    async def response(_request):
        if rate_limited:
            return web.json_response(
                {"ok": False, "description": "limited", "parameters": {"retry_after": 7}},
                status=429,
            )
        return web.json_response(
            {
                "ok": True,
                "result": {"message_id": 7, "date": 1, "chat": {"id": 1, "type": "private"}},
            }
        )

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", response)
    async with TestServer(app) as server:
        bot = Bot(TEST_TOKEN, session=SingleRequestSession(api=_api(server)))
        try:
            await _assert_response(bot, rate_limited)
        finally:
            await bot.session.close()


async def _assert_response(bot, rate_limited):
    if rate_limited:
        with pytest.raises(TelegramRetryAfter) as raised:
            await bot.send_message(chat_id=1, text="test")
        assert raised.value.retry_after == 7
    else:
        result = await bot.send_message(chat_id=1, text="test")
        assert result.message_id == 7 and result.chat.id == 1


async def test_single_request_session_keeps_response_timeout_unknown():
    async def slow_response(_request):
        await asyncio.sleep(0.1)
        return web.json_response({"ok": True, "result": True})

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", slow_response)
    async with TestServer(app) as server:
        bot = Bot(TEST_TOKEN, session=SingleRequestSession(api=_api(server), timeout=0.01))
        try:
            with pytest.raises(TelegramNetworkError) as raised:
                await bot.send_message(chat_id=1, text="test")
            assert not failed_before_single_request("send_progress_notice", raised.value)
        finally:
            await bot.session.close()
