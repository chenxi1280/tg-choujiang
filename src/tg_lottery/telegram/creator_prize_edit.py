"""Views and field replacement for an existing, unpublished draft prize."""

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.prize_limits import require_supported_winner_count

PRIZE_EDIT_KEYS = frozenset({"prize_edit_index", "prize_edit_original"})
PRIZE_EDIT_STATES = frozenset({"draft:prize_edit_name", "draft:prize_edit_count"})


def without_prize_edit(data):
    return {key: value for key, value in data.items() if key not in PRIZE_EDIT_KEYS}


def editing_prize(data):
    if data.get("published_campaign_id") or data.get("publish_snapshot"):
        raise UserFacingError("本场已经确认发布，不能修改奖品")
    index = data["prize_edit_index"]
    prizes = data.get("prizes") or []
    if index >= len(prizes) or prizes[index] != data["prize_edit_original"]:
        raise UserFacingError("这项奖品已经变化，请返回奖品列表重新选择")
    return index, prizes[index]


def prize_edit_view(data):
    index, prize = editing_prize(data)
    return (
        f"修改第 {index + 1} 项奖品\n\n名称：{prize['name']}\n中奖人数：{prize['winner_count']} 人",
        [
            [("修改名称", "draft_prize_edit_name"), ("修改中奖人数", "draft_prize_edit_count")],
            [("返回奖品", "draft_prizes")],
        ],
    )


def prize_edit_input_view(data, state):
    index, prize = editing_prize(data)
    label = "名称（最多120字）" if state == "draft:prize_edit_name" else "中奖人数（正整数人）"
    return (
        f"修改第 {index + 1} 项奖品 · {prize['name']}\n\n"
        f"请回复新的{label}。回复通过检查后立即保存。",
        [[("取消", "draft_prize_edit_cancel")]],
    )


def replace_prize_field(data, *, field, value):
    index, prize = editing_prize(data)
    prizes = list(data["prizes"])
    changed = prize | {field: value}
    prizes[index] = changed
    if field == "winner_count":
        require_supported_winner_count(item["winner_count"] for item in prizes)
    return data | {"prizes": prizes, "prize_edit_original": changed}
