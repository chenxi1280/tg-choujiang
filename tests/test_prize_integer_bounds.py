import pytest

from tests.test_v32_creator_controls import _prize_fixture
from tg_lottery.application.campaigns import CampaignService, PrizeSpec
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.draft_config import new_draft


@pytest.mark.parametrize("counts", [(2**31,), (2**31 - 1, 1)])
def test_service_rejects_overflowing_prize_and_slot_counts(counts):
    service = CampaignService(None)
    with pytest.raises(UserFacingError, match="中奖总人数不能超过"):
        service._validated_prizes(tuple(PrizeSpec("奖品", n, "领奖说明") for n in counts))


def test_service_accepts_int32_boundary_without_changing_counts():
    prize = PrizeSpec("奖品", 2**31 - 1, "领奖说明")
    assert CampaignService(None)._validated_prizes((prize,)) == (prize,)


@pytest.mark.parametrize("existing,count", [(0, 2**31), (2**31 - 1, 1)])
async def test_oversized_prize_input_keeps_pending_name_and_existing_prizes(existing, count):
    data = new_draft() | {
        "pending_prize_name": "新奖品",
        "prizes": [{"name": "原奖品", "winner_count": existing}] if existing else [],
    }
    flow, sessions, message = _prize_fixture(data)
    message.text = str(count)
    with pytest.raises(UserFacingError, match="中奖总人数不能超过"):
        await flow.receive_count(message, data)
    sessions.set.assert_not_awaited()
    assert data["pending_prize_name"] == "新奖品"
