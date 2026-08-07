import pytest
from pydantic import ValidationError

from tg_lottery.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456789:abcdefghijklmnopqrstuvwxyz",
        "database_url": "postgresql+asyncpg://user:password@db/app",
        "app_encryption_key": "A" * 44,
    }
    values.update(overrides)
    return Settings(**values)


def test_bot_id_is_derived_from_token() -> None:
    assert _settings().bot_id == 123456789
    assert _settings(bot_id="").bot_id == 123456789


def test_blank_admin_ids_env_is_empty_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_IDS", "")

    assert _settings().admin_ids == frozenset()


def test_explicit_bot_id_must_match_token() -> None:
    with pytest.raises(ValidationError, match="BOT_ID does not match BOT_TOKEN"):
        _settings(bot_id=987654321)


def test_token_requires_numeric_bot_id_prefix() -> None:
    with pytest.raises(ValidationError, match="numeric Telegram bot id"):
        _settings(bot_token="invalid-token-without-numeric-prefix")
