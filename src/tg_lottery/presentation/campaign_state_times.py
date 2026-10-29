from datetime import datetime

from tg_lottery.presentation.campaign_card import card_time


def campaign_state_times(
    *,
    cutoff_at: datetime,
    draw_at: datetime,
    timezone: str,
    completed_at: datetime | None = None,
) -> str:
    cutoff = card_time(cutoff_at.isoformat(), timezone)
    draw = card_time((completed_at or draw_at).isoformat(), timezone)
    draw_label = "实际开奖" if completed_at else "计划开奖"
    return f"北京时间：\n实际截止：{cutoff}\n{draw_label}：{draw}"
