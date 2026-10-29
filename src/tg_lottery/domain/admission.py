from datetime import datetime, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

PARTICIPATION_RULES_VERSION = "v3.0"


class MessageAdmissionRule(BaseModel, frozen=True, extra="forbid"):
    daily_messages: int = Field(default=20, ge=1)
    consecutive_days: int = Field(default=1, ge=1)
    # Retained only to read frozen legacy rules; message counting ignores this value.
    min_text_length: int = Field(default=4, ge=1)


class InviteAdmissionRule(BaseModel, frozen=True, extra="forbid"):
    required_valid_invites: int = Field(default=1, ge=1)


class AdmissionGroups(BaseModel, frozen=True, extra="forbid"):
    @model_validator(mode="after")
    def validate_groups(self):
        if not self.groups:
            raise ValueError("admission requires at least one participation group")
        if any(not key.lstrip("-").isdigit() for key in self.groups):
            raise ValueError("admission group ids must be numeric")
        return self


class MessageAdmission(AdmissionGroups):
    type: Literal["message"]
    groups: dict[str, MessageAdmissionRule]


class InviteAdmission(AdmissionGroups):
    type: Literal["invite"]
    groups: dict[str, InviteAdmissionRule]


Admission = Annotated[MessageAdmission | InviteAdmission, Field(discriminator="type")]


def validate_admission_window(rules: object, *, start_at: datetime, cutoff_at: datetime) -> None:
    admission = rules.admission
    if admission is None:
        return
    if start_at > cutoff_at:
        raise ValueError("本场报名截止已过, 无法完成门槛")
    if admission.type == "invite":
        deadline = cutoff_at - timedelta(hours=rules.invite.retention_hours)
        if start_at > deadline:
            raise ValueError("邀请门槛无法在截止前完成留存, 请调整开奖时间或有效邀请要求")
        return
    timezone = ZoneInfo(rules.timezone)
    first_day = start_at.astimezone(timezone).date()
    last_day = cutoff_at.astimezone(timezone).date()
    for chat_id, rule in admission.groups.items():
        if getattr(rules, "message_task_calendar", None):
            if (last_day - first_day).days + 1 != rule.consecutive_days:
                raise ValueError("固定任务日期数必须与发言门槛天数一致")
        if first_day + timedelta(days=rule.consecutive_days - 1) > last_day:
            raise ValueError(f"群 {chat_id} 的连续发言门槛无法在截止前完成, 请调整时间或天数")
