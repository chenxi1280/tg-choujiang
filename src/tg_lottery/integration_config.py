"""Explicit opt-in settings for the private, read-only operations integration."""

from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOTTERY_INTEGRATION_", extra="ignore")

    token: SecretStr = Field(min_length=32)
    creator_ids: Annotated[frozenset[int], NoDecode]
    host: str = "127.0.0.1"
    port: int = Field(default=8094, ge=1, le=65535)

    @field_validator("creator_ids", mode="before")
    @classmethod
    def parse_creators(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("creator_ids")
    @classmethod
    def require_creators(cls, value: frozenset[int]) -> frozenset[int]:
        if not value or min(value) <= 0:
            raise ValueError("integration requires explicit positive creator IDs")
        return value
