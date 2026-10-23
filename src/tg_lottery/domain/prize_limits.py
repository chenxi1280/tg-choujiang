from collections.abc import Iterable

from tg_lottery.domain.errors import UserFacingError

MAX_WINNER_COUNT = 2**31 - 1


def require_supported_winner_count(counts: Iterable[int]) -> None:
    if sum(counts) > MAX_WINNER_COUNT:
        raise UserFacingError(f"本场中奖总人数不能超过 {MAX_WINNER_COUNT} 人, 请减少人数后重试")
