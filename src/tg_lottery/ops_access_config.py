from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

OperatorId = Annotated[int, Field(gt=0)]


class OpsAccessSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ops_admin_ids: Annotated[frozenset[OperatorId], NoDecode] = frozenset()
    database_url: str

    @field_validator("ops_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value
