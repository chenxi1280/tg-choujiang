from tg_lottery.domain.errors import UserFacingError


def positive_integer(text: str) -> int:
    value = text.strip()
    if not value.isdigit() or int(value) < 1:
        raise UserFacingError("请输入正整数，不能使用 0、负数、小数或空值")
    return int(value)


def changed_positive(current: int, delta: int) -> int:
    return positive_integer(str(current + delta))


def rule_key(kind: str, field: str) -> str:
    if field == "reward" and kind == "activity":
        return "reward_tickets"
    if field not in {"cycle", "cap"}:
        raise UserFacingError("加成字段已失效，请重新打开编辑页")
    if field == "cycle":
        return "invites_per_cycle" if kind == "invite" else "messages_per_cycle"
    return "max_bonus_tickets" if kind == "invite" else "daily_max_cycles"


def input_label(kind: str, field: str) -> str:
    rule_key(kind, field)
    activity = {
        "cycle": "每轮发言条数（条）",
        "reward": "每轮奖励次数（次）",
        "cap": "每日上限轮数（轮）",
    }
    invite = {"cycle": "每轮有效邀请人数（人）", "cap": "累计奖励上限次数（次）"}
    return (activity if kind == "activity" else invite)[field]
