"""Explicit opt-in settings for the private, read-only operations integration."""

import re
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class IntegrationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOTTERY_INTEGRATION_", extra="ignore")

    token: SecretStr = Field(min_length=32)
    creator_ids: Annotated[frozenset[int], NoDecode]
    campaign_refs: Annotated[frozenset[str], NoDecode] = frozenset()
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

    @field_validator("campaign_refs", mode="before")
    @classmethod
    def parse_campaign_refs(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("campaign_refs")
    @classmethod
    def validate_campaign_refs(cls, value: frozenset[str]) -> frozenset[str]:
        if len(value) > 100 or any(not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", ref) for ref in value):
            raise ValueError("integration campaign reference scope is invalid")
        return value
