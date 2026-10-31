def bonus_menu_view(data: dict, *, summary: str):
    return (
        "📈 抽奖加成票设置\n\n以下只增加抽奖次数，不能替代参与条件或本场门槛。\n"
        f"当前设置：{summary}",
        [
            [("抽奖加成票(发言)", "draft_bonus_activity"), ("邀请加成票", "draft_bonus_invite")],
            [("返回草稿", "draft_back")],
        ],
    )
