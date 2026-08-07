from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(min_length=20)
    bot_id: int
    database_url: str
    app_encryption_key: str = Field(min_length=43, max_length=44)
    admin_ids: frozenset[int] = frozenset()
    timezone: str = "Asia/Shanghai"
    log_level: str = "INFO"
    poll_timeout_seconds: int = Field(default=30, ge=1, le=50)
    poll_lease_seconds: int = Field(default=60, ge=20, le=300)
    worker_poll_seconds: float = Field(default=2, ge=0.2, le=30)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
