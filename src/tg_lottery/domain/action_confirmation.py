from dataclasses import dataclass

from tg_lottery.domain.errors import UserFacingError


@dataclass(frozen=True)
class ExpectedCampaign:
    state: str
    revision: int

    def validate(self, campaign) -> None:
        if str(campaign.state) != self.state or campaign.config_revision != self.revision:
            raise UserFacingError("活动状态或版本已变化,确认未执行,请重新核对当前活动")


def validate_expected(campaign, expected: ExpectedCampaign | None) -> None:
    if expected is not None:
        expected.validate(campaign)
