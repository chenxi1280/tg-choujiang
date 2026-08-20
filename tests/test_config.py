import pytest
from pydantic import ValidationError

from tg_lottery.config import Settings
from tg_lottery.ops_bot_config import OpsBotSettings


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


def test_polling_lease_must_cover_long_poll_timeout() -> None:
    with pytest.raises(ValidationError, match="cover poll timeout"):
        _settings(poll_timeout_seconds=50, poll_lease_seconds=59)

    assert _settings(poll_timeout_seconds=50, poll_lease_seconds=60).poll_lease_seconds == 60


def test_ops_bot_requires_dedicated_identity_and_non_empty_allowlist() -> None:
    settings = OpsBotSettings(
        ops_bot_token="987654321:abcdefghijklmnopqrstuvwxyz",
        ops_admin_ids="101,202",
        database_url="postgresql+asyncpg://user:password@db/app",
    )

    assert settings.ops_bot_id == 987654321
    assert settings.ops_admin_ids == frozenset({101, 202})

    with pytest.raises(ValidationError, match="at least one"):
        OpsBotSettings(
            ops_bot_token="987654321:abcdefghijklmnopqrstuvwxyz",
            ops_admin_ids="",
            database_url="postgresql+asyncpg://user:password@db/app",
        )


def test_ops_bot_explicit_id_must_match_token() -> None:
    with pytest.raises(ValidationError, match="OPS_BOT_ID does not match"):
        OpsBotSettings(
            ops_bot_token="987654321:abcdefghijklmnopqrstuvwxyz",
            ops_bot_id=123,
            ops_admin_ids="101",
            database_url="postgresql+asyncpg://user:password@db/app",
        )
