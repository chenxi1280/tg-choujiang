from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    MAX_ACTIVITY_MESSAGE_THRESHOLD,
    MAX_ACTIVITY_REWARD_TICKETS,
    MAX_INVITE_BONUS_TICKETS,
    MAX_INVITE_COUNT,
)

ButtonRows = list[list[tuple[str, str]]]

BONUS_ACTIONS = frozenset(
    {
        "draft_bonus_toggle_invite",
        "draft_bonus_toggle_activity",
        "draft_bonus_invite",
        "draft_bonus_invite_count_dec",
        "draft_bonus_invite_count_inc",
        "draft_bonus_invite_total_dec",
        "draft_bonus_invite_total_inc",
        "draft_bonus_invite_add",
        "draft_bonus_invite_remove",
        "draft_bonus_activity",
        "draft_bonus_activity_threshold_dec",
        "draft_bonus_activity_threshold_inc",
        "draft_bonus_activity_reward_dec",
        "draft_bonus_activity_reward_inc",
        "draft_bonus_menu",
    }
)


def bonus_text(data: dict[str, Any]) -> str:
    return "🔋 抽奖加成\n\n当前设置：" + bonus_summary(data) + "\n\n请直接点击按钮配置。"


def bonus_summary(data: dict[str, Any]) -> str:
    types = set(data.get("bonus_types", []))
    if not types:
        return "关闭"
    summaries = []
    if "invite" in types:
        tiers = data.get("invite_reward_tiers", [])
        values = ", ".join(f"{item['invite_count']}人+{item['total_bonus']}次" for item in tiers)
        summaries.append(f"邀请({values}，上限{data['invite_bonus_cap']}次)")
    if "activity" in types:
        summaries.append(
            f"发言(每个条件群每天{data['activity_message_threshold']}条，"
            f"全部达标+{data['activity_reward_tickets']}次)"
        )
    return "；".join(summaries)


def bonus_rows(data: dict[str, Any]) -> ButtonRows:
    types = set(data.get("bonus_types", []))
    invite = "✅ 邀请加成" if "invite" in types else "⬜ 邀请加成"
    activity = "✅ 发言加成" if "activity" in types else "⬜ 发言加成"
    rows = [[(invite, "draft_bonus_toggle_invite"), (activity, "draft_bonus_toggle_activity")]]
    if "invite" in types:
        rows.append([("设置邀请阶梯", "draft_bonus_invite")])
    if "activity" in types:
        rows.append([("设置每日发言", "draft_bonus_activity")])
    rows.append([("↩ 返回抽奖预览", "draft_back")])
    return rows


def invite_bonus_text(data: dict[str, Any]) -> str:
    return (
        "👥 邀请抽奖加成\n\n"
        f"当前阶梯：{_tier_summary(data)}\n"
        "每档表示累计有效邀请人数对应的累计抽奖加成。"
    )


def invite_bonus_rows(data: dict[str, Any]) -> ButtonRows:
    last = _tiers(data)[-1]
    return [
        [
            ("邀请人数 −", "draft_bonus_invite_count_dec"),
            (f"{last['invite_count']} 人", "draft_bonus_invite"),
            ("＋", "draft_bonus_invite_count_inc"),
        ],
        [
            ("累计次数 −", "draft_bonus_invite_total_dec"),
            (f"+{last['total_bonus']} 次", "draft_bonus_invite"),
            ("＋", "draft_bonus_invite_total_inc"),
        ],
        [("新增下一档", "draft_bonus_invite_add"), ("删除最后一档", "draft_bonus_invite_remove")],
        [("↩ 返回加成设置", "draft_bonus_menu")],
    ]


def activity_bonus_text(data: dict[str, Any]) -> str:
    return (
        "💬 发言抽奖加成\n\n"
        f"在每个条件群每天分别发送达到 {data['activity_message_threshold']} 条有效消息，"
        f"全部达标后当天增加 {data['activity_reward_tickets']} 次。\n"
        "频道只要求关注，不要求发言。\n"
        "每天单独结算，不要求连续签到。"
    )


def activity_bonus_rows(data: dict[str, Any]) -> ButtonRows:
    return [
        [
            ("消息数 −", "draft_bonus_activity_threshold_dec"),
            (str(data["activity_message_threshold"]), "draft_bonus_activity"),
            ("＋", "draft_bonus_activity_threshold_inc"),
        ],
        [
            ("加成 −", "draft_bonus_activity_reward_dec"),
            (f"+{data['activity_reward_tickets']} 次", "draft_bonus_activity"),
            ("＋", "draft_bonus_activity_reward_inc"),
        ],
        [("↩ 返回加成设置", "draft_bonus_menu")],
    ]


def apply_bonus_action(data: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "draft_bonus_toggle_invite":
        return _toggle_type(data, "invite")
    if action == "draft_bonus_toggle_activity":
        return _toggle_type(data, "activity")
    if action.startswith("draft_bonus_invite_"):
        return _change_invite(data, action)
    if action.startswith("draft_bonus_activity_"):
        return _change_activity(data, action)
    return dict(data)


def bonus_view(data: dict[str, Any], action: str) -> tuple[str, ButtonRows]:
    if action.startswith("draft_bonus_invite"):
        return invite_bonus_text(data), invite_bonus_rows(data)
    if action.startswith("draft_bonus_activity"):
        return activity_bonus_text(data), activity_bonus_rows(data)
    return bonus_text(data), bonus_rows(data)


def _toggle_type(data: dict[str, Any], bonus_type: str) -> dict[str, Any]:
    types = list(dict.fromkeys(data.get("bonus_types", [])))
    types = (
        [item for item in types if item != bonus_type]
        if bonus_type in types
        else [*types, bonus_type]
    )
    return data | {"bonus_types": types, "luck_mode": "standard" if types else "off"}


def _change_invite(data: dict[str, Any], action: str) -> dict[str, Any]:
    tiers = [dict(item) for item in _tiers(data)]
    if action == "draft_bonus_invite_add":
        return _invite_result(data, _add_tier(tiers))
    if action == "draft_bonus_invite_remove":
        return _invite_result(data, _remove_tier(tiers))
    if action.endswith("count_dec"):
        change = ("invite_count", -1, _previous(tiers, "invite_count") + 1, MAX_INVITE_COUNT)
        return _change_last_tier(data, tiers, change)
    if action.endswith("count_inc"):
        return _change_last_tier(data, tiers, ("invite_count", 1, 1, MAX_INVITE_COUNT))
    if action.endswith("total_dec"):
        change = (
            "total_bonus",
            -1,
            _previous(tiers, "total_bonus") + 1,
            MAX_INVITE_BONUS_TICKETS,
        )
        return _change_last_tier(data, tiers, change)
    if action.endswith("total_inc"):
        return _change_last_tier(data, tiers, ("total_bonus", 1, 1, MAX_INVITE_BONUS_TICKETS))
    return data


def _change_last_tier(
    data: dict[str, Any], tiers: list[dict[str, int]], change: tuple[str, int, int, int]
) -> dict[str, Any]:
    key, delta, lower, upper = change
    tiers[-1][key] = _bounded(tiers[-1][key] + delta, lower, upper)
    return _invite_result(data, tiers)


def _remove_tier(tiers: list[dict[str, int]]) -> list[dict[str, int]]:
    if len(tiers) == 1:
        raise UserFacingError("邀请加成至少保留一个阶梯；如需关闭请返回取消勾选")
    return tiers[:-1]


def _invite_result(data: dict[str, Any], tiers: list[dict[str, int]]) -> dict[str, Any]:
    return data | {"invite_reward_tiers": tiers, "invite_bonus_cap": tiers[-1]["total_bonus"]}


def _change_activity(data: dict[str, Any], action: str) -> dict[str, Any]:
    key = "activity_message_threshold" if "threshold" in action else "activity_reward_tickets"
    upper = MAX_ACTIVITY_MESSAGE_THRESHOLD if "threshold" in action else MAX_ACTIVITY_REWARD_TICKETS
    delta = -1 if action.endswith("dec") else 1
    return data | {key: _bounded(int(data[key]) + delta, 1, upper)}


def _add_tier(tiers: list[dict[str, int]]) -> list[dict[str, int]]:
    last = tiers[-1]
    if last["invite_count"] >= MAX_INVITE_COUNT or last["total_bonus"] >= MAX_INVITE_BONUS_TICKETS:
        raise UserFacingError("邀请阶梯已经达到配置上限")
    return [
        *tiers,
        {"invite_count": last["invite_count"] + 1, "total_bonus": last["total_bonus"] + 1},
    ]


def _tiers(data: dict[str, Any]) -> list[dict[str, int]]:
    return [
        dict(item)
        for item in data.get("invite_reward_tiers") or [{"invite_count": 1, "total_bonus": 1}]
    ]


def _previous(tiers: list[dict[str, int]], key: str) -> int:
    return tiers[-2][key] if len(tiers) > 1 else 0


def _bounded(value: int, lower: int, upper: int) -> int:
    if value < lower or value > upper:
        raise UserFacingError(f"该数值必须在 {lower}–{upper} 之间")
    return value


def _tier_summary(data: dict[str, Any]) -> str:
    return "、".join(
        f"{item['invite_count']} 人 +{item['total_bonus']} 次" for item in _tiers(data)
    )
