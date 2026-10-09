from tg_lottery.domain.errors import UserFacingError

CONFIRMATION_STATES = frozenset({"draft:prize_delete_confirm", "draft:prize_clear_confirm"})


def delete_confirmation(data):
    positions = data.get("pending_prize_delete") or []
    prizes = data.get("prizes") or []
    if not positions or any(index < 1 or index > len(prizes) for index in positions):
        raise UserFacingError("奖品删除范围已失效，请重新选择要删除的奖品")
    names = [p["name"] for i, p in enumerate(prizes, 1) if i in positions]
    return (
        "确认删除奖品\n\n将删除：" + "、".join(names) + "\n其他奖品不变。",
        [[("确认删除指定奖品", "draft_delete_prize_confirm")], [("保留奖品", "draft_prizes")]],
    )


def clear_confirmation(data):
    return (
        f"确认清空奖品\n\n将移除本草稿的{len(data.get('prizes') or [])}项奖品，其他配置保留。",
        [[("确认清空奖品", "draft_clear_prizes_confirm")], [("保留奖品", "draft_prizes")]],
    )


def confirmation_view(data, state):
    return (
        delete_confirmation(data)
        if state == "draft:prize_delete_confirm"
        else clear_confirmation(data)
    )
