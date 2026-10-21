"""Explicit frozen platform requirements, independent of campaign contract versions."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

POLICY_ROLES = {
    "official-v1": frozenset({"guide", "navigation", "featured"}),
    "official-v2": frozenset({"navigation", "featured"}),
}


class OfficialChannel(BaseModel, frozen=True):
    role: Literal["guide", "navigation", "featured"]
    chat_id: int = Field(lt=0)
    title: str = Field(min_length=1, max_length=120)
    join_url: str

    @field_validator("join_url")
    @classmethod
    def valid_join_url(cls, value):
        if not re.fullmatch(
            r"https://t\.me/(?:[A-Za-z][A-Za-z0-9_]{4,31}|\+[A-Za-z0-9_-]+)", value
        ):
            raise ValueError("official channel requires a channel join URL, not a message URL")
        return value

    def record(self):
        return {
            "id": self.chat_id,
            "title": self.title,
            "type": "channel",
            "join_url": self.join_url,
            "private": "/+" in self.join_url,
            "username": None if "/+" in self.join_url else self.join_url.rsplit("/", 1)[1],
            "sources": ["platform"],
            "platform_role": self.role,
        }


class PlatformPolicy(BaseModel, frozen=True):
    version: Literal["official-v1", "official-v2"] = "official-v1"
    bot_id: int = Field(gt=0)
    channels: tuple[OfficialChannel, ...]
    max_observation_age_seconds: int = Field(default=300, ge=30, le=900)

    @model_validator(mode="after")
    def validate_channels(self):
        roles = POLICY_ROLES[self.version]
        if len(self.channels) != len(roles) or {item.role for item in self.channels} != roles:
            raise ValueError(f"{self.version} requires official channel roles: {sorted(roles)}")
        if len({item.chat_id for item in self.channels}) != len(roles):
            raise ValueError("official channels must have distinct stable identities")
        if len({item.join_url for item in self.channels}) != len(roles):
            raise ValueError("official channels must have distinct join URLs")
        return self

    @property
    def chat_ids(self):
        return tuple(item.chat_id for item in self.channels)


def merge_official_targets(requirements, policy):
    if policy is None:
        return list(requirements)
    official = {item.chat_id: item.record() for item in policy.channels}
    extras = []
    for item in requirements:
        chat_id = int(item["id"])
        if chat_id in official:
            if item.get("sources") != ["platform"]:
                official[chat_id]["sources"] = ["platform", "creator"]
        else:
            extras.append(item | {"sources": ["creator"]})
    return [*official.values(), *extras]
