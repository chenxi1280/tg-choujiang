from datetime import UTC, datetime

import pytest

from tg_lottery.domain.enums import CampaignState, QualificationState
from tg_lottery.domain.errors import (
    AuthorizationDenied,
    RegionLockedError,
    UserFacingError,
)
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
    format_public_time,
    public_error_text,
    qualification_state_text,
)


def test_every_campaign_state_has_a_non_technical_public_label() -> None:
    labels = {state: campaign_state_text(state) for state in CampaignState}

    assert len(labels) == len(CampaignState)
    assert all(label != state.value for state, label in labels.items())
    assert labels[CampaignState.RUNNING] == "进行中"
    assert labels[CampaignState.READY_TO_DRAW] == "待开奖"


def test_every_qualification_state_has_a_non_technical_public_label() -> None:
    labels = {state: qualification_state_text(state) for state in QualificationState}

    assert len(labels) == len(QualificationState)
    assert all(label != state.value for state, label in labels.items())
    assert labels[QualificationState.ELIGIBLE] == "符合条件"


def test_public_time_requires_and_displays_the_authoritative_timezone() -> None:
    instant = datetime(2026, 8, 8, 3, 25, tzinfo=UTC)

    assert format_public_time(instant, "Asia/Shanghai") == ("2026年08月08日 11:25 (北京时间)")
    assert format_public_time("2026-08-08 11:25", "Asia/Shanghai") == (
        "2026年08月08日 11:25 (北京时间)"
    )
    with pytest.raises(ValueError, match="explicit timezone"):
        format_public_time(instant, "")


def test_public_time_never_exposes_an_unmapped_iana_timezone() -> None:
    instant = datetime(2026, 8, 8, 3, 25, tzinfo=UTC)

    text = format_public_time(instant, "Europe/London")

    assert text == "2026年08月08日 04:25 (活动当地时间)"
    assert "Europe/London" not in text


def test_public_error_projection_only_exposes_explicitly_reviewed_details() -> None:
    secret = "unknown callback action: 8f76df07-3bb2-4431-bd67-525f9209bbd8"

    assert public_error_text(UserFacingError("奖品名称过长"), "Asia/Shanghai") == ("奖品名称过长")
    assert public_error_text(ValueError(secret), "Asia/Shanghai") == (
        "系统暂时无法完成此操作, 请稍后重试。"
    )
    assert secret not in public_error_text(ValueError(secret), "Asia/Shanghai")
    assert public_error_text(AuthorizationDenied(secret), "Asia/Shanghai") == (
        "没有执行此操作的权限, 或页面授权已失效。"
    )


def test_region_lock_uses_the_bot_business_timezone_without_raw_datetime() -> None:
    error = RegionLockedError(datetime(2026, 9, 7, 0, 0, tzinfo=UTC))

    assert public_error_text(error, "Asia/Shanghai") == (
        "地区已锁定, 请于 2026年09月07日 08:00 (北京时间) 后修改。"
    )
