from tg_lottery.presentation.daily_bonus import daily_bonus_rule_text


def bonus_menu_view(data: dict, *, summary: str):
    policy = (
        "\n\n" + daily_bonus_rule_text(data.get("activity_bonus_double_count", True))
        if data.get("bonus_policy_version") == "daily-after-start-v1"
        else ""
    )
    return (
        "📈 抽奖加成票设置\n\n以下只增加抽奖次数，不能替代参与条件或本场门槛。\n"
        f"当前设置：{summary}{policy}",
        [
            [("抽奖加成票(发言)", "draft_bonus_activity"), ("邀请加成票", "draft_bonus_invite")],
            [("返回草稿", "draft_back")],
        ],
    )
