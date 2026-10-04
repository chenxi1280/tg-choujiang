from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    MAX_ACTIVITY_MESSAGE_THRESHOLD,
    MAX_ACTIVITY_REWARD_TICKETS,
    MAX_INVITE_BONUS_TICKETS,
    MAX_INVITE_COUNT,
)
from tg_lottery.telegram import creator_bonus_copy as bonus_copy
from tg_lottery.telegram import creator_group_bonus as group_bonus
from tg_lottery.telegram import creator_invite_retention as retention

ButtonRows = list[list[tuple[str, str]]]

BONUS_ACTIONS = (
    frozenset(
        {
            "draft_bonus_toggle_invite",
            "draft_bonus_toggle_activity",
            "draft_bonus_invite",
            "draft_bonus_invite_mode_ratio",
            "draft_bonus_invite_mode_tiered",
            "draft_bonus_invite_ratio_dec",
            "draft_bonus_invite_ratio_inc",
            "draft_bonus_invite_cap_dec",
            "draft_bonus_invite_cap_inc",
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
    | retention.ACTIONS
)


def bonus_text(data: dict[str, Any]) -> str:
    return (
        "🔋 抽奖加成\n\n当前设置："
        + bonus_summary(data)
        + "\n邀请资格："
        + retention.retention_text(retention.retention_hours(data))
        + "\n\n请直接点击按钮配置。"
    )


def bonus_summary(data: dict[str, Any]) -> str:
    if data.get("participation_groups") and data.get("bonus_types"):
        return _configured_group_summary(data)
    if data.get("group_invite_rules") or data.get("group_activity_rules"):
        return group_bonus.summary(data)
    types = set(data.get("bonus_types", []))
    if not types:
        return "关闭"
    summaries = []
    if "invite" in types:
        summaries.append(_invite_summary(data))
    if "activity" in types:
        summaries.append(
            f"发言(每个条件群每天{data['activity_message_threshold']}条，"
            f"全部达标+{data['activity_reward_tickets']}次)"
        )
    return "；".join(summaries)


def _configured_group_summary(data: dict[str, Any]) -> str:
    pending = []
    for kind, label in (("invite", "邀请"), ("activity", "发言")):
        configured = data.get(f"group_{kind}_rules") or {}
        if kind in data["bonus_types"] and not any(
            rule.get("enabled") for rule in configured.values()
        ):
            pending.append(f"{label}加成待逐群设置")
    active = group_bonus.summary(data)
    return "；".join(([active] if active != "关闭" else []) + pending)


def bonus_rows(data: dict[str, Any]) -> ButtonRows:
    types = set(data.get("bonus_types", []))
    invite = "✅ 邀请加成" if "invite" in types else "⬜ 邀请加成"
    activity = "✅ 发言加成" if "activity" in types else "⬜ 发言加成"
    rows = [[(invite, "draft_bonus_toggle_invite"), (activity, "draft_bonus_toggle_activity")]]
    if "invite" in types:
        rows.append([("设置邀请加成", "draft_bonus_invite")])
        rows.append([("邀请留存要求", "draft_retention")])
    if "activity" in types:
        rows.append([("设置每日发言", "draft_bonus_activity")])
    rows.append([("↩ 返回抽奖预览", "draft_back")])
    return rows


def invite_bonus_text(data: dict[str, Any]) -> str:
    if _invite_mode(data) == "ratio":
        return (
            "👥 邀请抽奖加成\n\n"
            f"每 {_ratio(data)} 个有效邀请 +1 次，最多 {data['invite_bonus_cap']} 次。"
        )
    return (
        "👥 邀请抽奖加成\n\n"
        f"当前阶梯：{_tier_summary(data)}\n"
        "每档表示累计有效邀请人数对应的累计抽奖加成。"
    )


def invite_bonus_rows(data: dict[str, Any]) -> ButtonRows:
    mode_rows = [
        [
            (
                "✅ 简单比例" if _invite_mode(data) == "ratio" else "简单比例",
                "draft_bonus_invite_mode_ratio",
            ),
            (
                "✅ 累计阶梯" if _invite_mode(data) == "tiered" else "累计阶梯",
                "draft_bonus_invite_mode_tiered",
            ),
        ]
    ]
    if _invite_mode(data) == "ratio":
        return mode_rows + _ratio_rows(data)
    last = _tiers(data)[-1]
    return [
        *mode_rows,
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
    _reject_legacy_invite_action(data, action)
    if action in retention.ACTIONS:
        return retention.apply_action(data, action)
    if group_bonus.handles_action(action):
        return group_bonus.apply_action(data, action)
    if action == "draft_bonus_toggle_invite":
        return _toggle_type(data, "invite")
    if action == "draft_bonus_toggle_activity":
        return _toggle_type(data, "activity")
    if action.startswith("draft_bonus_invite_mode_"):
        return data | {"invite_reward_mode": action.rsplit("_", 1)[-1]}
    if action.startswith("draft_bonus_invite_ratio_"):
        return _change_ratio(data, action)
    if action.startswith("draft_bonus_invite_cap_"):
        return _change_cap(data, action)
    if action.startswith("draft_bonus_invite_"):
        return _change_invite(data, action)
    if action.startswith("draft_bonus_activity_"):
        return _change_activity(data, action)
    return dict(data)


def _reject_legacy_invite_action(data: dict, action: str) -> None:
    if (
        data.get("participation_rules_version") == "v3.0"
        and action.startswith("draft_bonus_invite_")
        and not group_bonus.handles_action(action)
    ):
        raise UserFacingError("旧邀请配置入口已失效，请重新打开逐群邀请加成设置")


def bonus_view(data: dict[str, Any], action: str) -> tuple[str, ButtonRows]:
    if action in retention.ACTIONS:
        return retention.view(data)
    if data.get("bonus_copy"):
        return bonus_copy.copy_view(data)
    working = data.get("bonus_working_copy")
    if isinstance(working, dict):
        return group_bonus.editor_view(data, working)
    if action == "draft_bonus_copy_confirm":
        kind = str(data.get("bonus_copy_result_kind") or "invite")
        return group_bonus.list_view(data, kind)
    if action in {"draft_bonus_invite", "draft_bonus_invite_save"}:
        return group_bonus.list_view(data, "invite")
    if action in {"draft_bonus_activity", "draft_bonus_activity_save"}:
        return group_bonus.list_view(data, "activity")
    if action.startswith("draft_bonus_group_page:"):
        return group_bonus.list_view(data, group_bonus.page_kind(action))
    return _legacy_bonus_view(data, action)


def _legacy_bonus_view(data: dict[str, Any], action: str) -> tuple[str, ButtonRows]:
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


def _change_ratio(data: dict[str, Any], action: str) -> dict[str, Any]:
    delta = -1 if action.endswith("dec") else 1
    value = _bounded(_ratio(data) + delta, 1, MAX_INVITE_COUNT)
    return data | {"valid_invites_per_ticket": value}


def _change_cap(data: dict[str, Any], action: str) -> dict[str, Any]:
    delta = -1 if action.endswith("dec") else 1
    value = _bounded(int(data["invite_bonus_cap"]) + delta, 1, MAX_INVITE_BONUS_TICKETS)
    return data | {"invite_bonus_cap": value}


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


def _invite_mode(data: dict[str, Any]) -> str:
    return str(data.get("invite_reward_mode", "tiered"))


def _ratio(data: dict[str, Any]) -> int:
    return int(data.get("valid_invites_per_ticket", 2))


def _invite_summary(data: dict[str, Any]) -> str:
    if _invite_mode(data) == "ratio":
        return f"邀请(每{_ratio(data)}个有效邀请+1次，上限{data['invite_bonus_cap']}次)"
    tiers = data.get("invite_reward_tiers", [])
    values = ", ".join(f"{item['invite_count']}人+{item['total_bonus']}次" for item in tiers)
    return f"邀请({values}，上限{data['invite_bonus_cap']}次)"


def _ratio_rows(data: dict[str, Any]) -> ButtonRows:
    return [
        [
            ("邀请人数 −", "draft_bonus_invite_ratio_dec"),
            (f"每 {_ratio(data)} 人", "draft_bonus_invite"),
            ("＋", "draft_bonus_invite_ratio_inc"),
        ],
        [
            ("上限 −", "draft_bonus_invite_cap_dec"),
            (f"最多 {data['invite_bonus_cap']} 次", "draft_bonus_invite"),
            ("＋", "draft_bonus_invite_cap_inc"),
        ],
        [("↩ 返回加成设置", "draft_bonus_menu")],
    ]
