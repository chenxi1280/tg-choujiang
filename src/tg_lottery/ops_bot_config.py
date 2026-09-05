from functools import lru_cache

from pydantic import Field, SecretStr, field_validator, model_validator

from tg_lottery.ops_access_config import OpsAccessSettings


class OpsBotSettings(OpsAccessSettings):
    ops_bot_token: SecretStr = Field(min_length=20)
    ops_bot_id: int = 0
    log_level: str = "INFO"

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
        self.ops_bot_id = token_bot_id
        return self


@lru_cache(maxsize=1)
def get_ops_bot_settings() -> OpsBotSettings:
    return OpsBotSettings()  # type: ignore[call-arg]
