"""Telegram TLS profile with a single-segment ClientHello."""

from aiogram.client.session.aiohttp import AiohttpSession


class TelegramSession(AiohttpSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._connector_init["ssl"].set_ecdh_curve("X25519")
