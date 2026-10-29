def bonus_menu_view(data: dict, *, summary: str):
    return (
        f"📈 加成设置\n\n以下只增加抽奖次数，不能替代参与条件或本场门槛。\n当前设置：{summary}",
        [
            [("发言抽奖加成", "draft_bonus_activity"), ("邀请抽奖加成", "draft_bonus_invite")],
            [("返回草稿", "draft_back")],
        ],
    )
