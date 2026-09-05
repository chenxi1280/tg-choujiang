from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_POLL_LEASE_MARGIN_SECONDS = 10


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(min_length=20)
    bot_id: int = 0
    database_url: str
    app_encryption_key: str = Field(min_length=43, max_length=44)
    admin_ids: Annotated[frozenset[int], NoDecode] = frozenset()
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    poll_timeout_seconds: int = Field(default=30, ge=1, le=50)
    poll_lease_seconds: int = Field(default=60, ge=20, le=300)
    worker_poll_seconds: float = Field(default=2, ge=0.2, le=30)
    qualification_requests_per_second: float = Field(default=20, ge=1, le=30)
    release_sha: str = "unknown"
    release_id: str = "unknown"
    guide_channel_url: str = ""
    navigation_channel_url: str = ""
    featured_channel_url: str = ""

    @field_validator("guide_channel_url", "navigation_channel_url", "featured_channel_url")
    @classmethod
    def valid_channel_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "t.me"
            or not parsed.path.strip("/")
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            raise ValueError("channel URL must be an https://t.me/ channel or message link")
        return value

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("bot_id", mode="before")
    @classmethod
    def blank_bot_id_uses_token(cls, value: object) -> object:
        return 0 if value in {None, ""} else value

    @model_validator(mode="after")
    def derive_bot_id_and_validate_polling(self) -> "Settings":
        token_prefix, separator, _ = self.bot_token.partition(":")
        if not separator or not token_prefix.isdecimal():
            raise ValueError("BOT_TOKEN must start with the numeric Telegram bot id")
        token_bot_id = int(token_prefix)
        if self.bot_id not in {0, token_bot_id}:
            raise ValueError("BOT_ID does not match BOT_TOKEN")
        if self.poll_lease_seconds < self.poll_timeout_seconds + _POLL_LEASE_MARGIN_SECONDS:
            raise ValueError("POLL_LEASE_SECONDS must cover poll timeout plus 10 seconds")
        self.bot_id = token_bot_id
        return self

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
