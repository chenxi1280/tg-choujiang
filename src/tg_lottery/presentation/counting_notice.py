"""Explain why a member's current message does not count."""

from tg_lottery.telegram.public_projection import format_public_time


def blocked_counting_notice(campaign, *, registered):
    if registered:
        text = (
            f"「{campaign.name}」：你已报名，但目前有参与条件未满足，本条发言不计数。"
            "补齐条件并重新检查通过后恢复计数，未满足期间的发言不补算；"
            "已获得的加成保留，完成门槛并满足参与条件后生效。"
            "当前有效抽奖次数为 0。"
        )
    else:
        text = (
            f"「{campaign.name}」：报名尚未成功，本条发言不计数。"
            "全部入群、关注及其他参与条件通过并报名成功后，才在本期统计时间内开始计数，"
            "之前的发言不补算。"
        )
    return text + "\n本期统计截止：" + format_public_time(campaign.cutoff_at, campaign.timezone)
