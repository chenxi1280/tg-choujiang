from tg_lottery.domain.errors import UserFacingError


def positive_integer(text: str) -> int:
    value = text.strip()
    if not value.isdecimal():
        raise UserFacingError("请输入正整数，不能使用 0、负数、小数或空值")
    try:
        number = int(value)
    except ValueError as exc:
        raise UserFacingError("请输入可解析的正整数，数值过长时请缩短输入") from exc
    if number < 1:
        raise UserFacingError("请输入正整数，不能使用 0、负数、小数或空值")
    return number


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


def input_prompt_text(working, *, group_title: str, field: str) -> str:
    kind = str(working["kind"])
    label = input_label(kind, field)
    value = working["rule"].get(rule_key(kind, field))
    module = "邀请加成" if kind == "invite" else "活跃加成（人品）"
    return (
        f"{group_title} · {module}\n当前{label}：{value}\n\n请输入{label}（正整数，只发送数字）："
        "\n提交后返回本群编辑页，保存本群后生效；取消输入保留其他暂存设置。"
    )
