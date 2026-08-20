from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class OpsBotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ops_bot_token: SecretStr = Field(min_length=20)
    ops_bot_id: int = 0
    ops_admin_ids: Annotated[frozenset[int], NoDecode]
    database_url: str
    log_level: str = "INFO"

    @field_validator("ops_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("ops_bot_id", mode="before")
    @classmethod
    def blank_bot_id_uses_token(cls, value: object) -> object:
        return 0 if value in {None, ""} else value

    @model_validator(mode="after")
    def validate_bot_identity_and_allowlist(self) -> "OpsBotSettings":
        token_prefix, separator, _ = self.ops_bot_token.get_secret_value().partition(":")
        if not separator or not token_prefix.isdecimal():
            raise ValueError("OPS_BOT_TOKEN must start with the numeric Telegram bot id")
        token_bot_id = int(token_prefix)
        if self.ops_bot_id not in {0, token_bot_id}:
            raise ValueError("OPS_BOT_ID does not match OPS_BOT_TOKEN")
        if not self.ops_admin_ids:
            raise ValueError("OPS_ADMIN_IDS must contain at least one Telegram user id")
        self.ops_bot_id = token_bot_id
        return self


@lru_cache(maxsize=1)
def get_ops_bot_settings() -> OpsBotSettings:
    return OpsBotSettings()  # type: ignore[call-arg]
