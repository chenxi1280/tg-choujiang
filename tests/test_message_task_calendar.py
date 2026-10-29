from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.v30_helpers import FIRST, rules_for
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.message_calendar import calendar_progress

DATES = tuple(date(2026, 10, 29) + timedelta(days=n) for n in range(3))


@pytest.mark.parametrize(
    "counts,today,days,qualified",
    [
        ({date(2026, 10, 27): 50, date(2026, 10, 28): 50, DATES[0]: 60}, DATES[0], 1, False),
        ({DATES[0]: 108}, DATES[0], 1, False),
        ({DATES[0]: 36, DATES[2]: 100}, DATES[2], 2, False),
        ({day: 36 for day in DATES}, DATES[0], 1, False),
        ({day: 36 for day in DATES}, DATES[2], 3, True),
    ],
)
def test_only_each_required_date_can_complete_the_calendar(counts, today, *, days, qualified):
    rule = SimpleNamespace(daily_messages=36, consecutive_days=3)
    result = calendar_progress(counts, rule, today=today, dates=DATES)
    assert result.consecutive_days == days
    assert result.qualified is qualified


def test_calendar_requires_message_gate_and_matching_window():
    from datetime import datetime

    from tg_lottery.domain.rules import CampaignRules

    with pytest.raises(ValidationError, match=r"requires V3\.0 message"):
        CampaignRules(message_task_calendar="campaign-days-v1")
    rules = rules_for("message", groups={str(FIRST): dict(daily_messages=36, consecutive_days=3)})
    rules = CampaignRules.model_validate(
        rules.model_dump() | {"message_task_calendar": "campaign-days-v1"}
    )
    with pytest.raises(ValueError, match="日期数"):
        validate_admission_window(
            rules,
            start_at=datetime.fromisoformat("2026-10-29T20:00:00+08:00"),
            cutoff_at=datetime.fromisoformat("2026-11-01T19:55:00+08:00"),
        )
