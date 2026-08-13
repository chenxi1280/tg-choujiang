import re
from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import (
    MAX_ACTIVITY_MESSAGE_THRESHOLD,
    MAX_ACTIVITY_REWARD_TICKETS,
    MAX_INVITE_BONUS_TICKETS,
    MAX_INVITE_COUNT,
)

_CLAUSE_SPLIT = re.compile(r"[；;\n]+")
_PAIR_SPLIT = re.compile(r"[,，、\s]+")
_PAIR = re.compile(r"(\d+)\s*[:=：]\s*(\d+)")
_CAP = re.compile(r"上限\s*[:=：]?\s*(\d+)")


def bonus_prompt_text(data: dict[str, Any]) -> str:
    return (
        "🔋 抽奖加成\n\n"
        f"当前设置：{bonus_summary(data)}\n\n"
        "请回复完整配置，支持以下写法：\n"
        "• 邀请 1:1,2:3,5:10 上限10\n"
        "• 发言 10:1\n"
        "• 邀请 1:1,2:3 上限3；发言 10:1\n"
        "• 关闭\n\n"
        "邀请配置表示累计有效邀请人数:累计增加次数；"
        "发言配置表示每日有效消息数:当日增加次数。"
    )


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
            f"发言(每天{data['activity_message_threshold']}条+{data['activity_reward_tickets']}次)"
        )
    return "；".join(summaries)


def parse_bonus_config(value: str) -> dict[str, Any]:
    text = value.strip()
    if text == "关闭":
        return {"bonus_types": [], "luck_mode": "off"}
    changes: dict[str, Any] = {"bonus_types": [], "luck_mode": "standard"}
    seen: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(text):
        normalized = clause.strip()
        if normalized.startswith("邀请"):
            _reject_duplicate(seen, "invite", "邀请")
            changes.update(_parse_invite(normalized.removeprefix("邀请").strip()))
            changes["bonus_types"].append("invite")
            continue
        if normalized.startswith("发言"):
            _reject_duplicate(seen, "activity", "发言")
            changes.update(_parse_activity(normalized.removeprefix("发言").strip()))
            changes["bonus_types"].append("activity")
            continue
        raise UserFacingError("抽奖加成配置需以“邀请”或“发言”开头")
    if not changes["bonus_types"]:
        raise UserFacingError("请至少配置一种抽奖加成，或回复“关闭”")
    return changes


def _parse_invite(value: str) -> dict[str, Any]:
    cap_match = _CAP.search(value)
    if cap_match is None:
        raise UserFacingError("邀请加成必须设置总上限，例如：上限10")
    pairs_text = _CAP.sub("", value).strip()
    tiers = [_tier(item) for item in _PAIR_SPLIT.split(pairs_text) if item]
    _validate_tiers(tiers, int(cap_match.group(1)))
    return {"invite_reward_tiers": tiers, "invite_bonus_cap": int(cap_match.group(1))}


def _parse_activity(value: str) -> dict[str, Any]:
    match = _PAIR.fullmatch(value)
    if match is None:
        raise UserFacingError("发言加成格式应为“每日消息数:增加次数”，例如：10:1")
    threshold = int(match.group(1))
    reward = int(match.group(2))
    if not 1 <= threshold <= MAX_ACTIVITY_MESSAGE_THRESHOLD:
        raise UserFacingError(f"每日消息数必须在 1–{MAX_ACTIVITY_MESSAGE_THRESHOLD} 之间")
    if not 1 <= reward <= MAX_ACTIVITY_REWARD_TICKETS:
        raise UserFacingError(f"每日增加次数必须在 1–{MAX_ACTIVITY_REWARD_TICKETS} 之间")
    return {"activity_message_threshold": threshold, "activity_reward_tickets": reward}


def _tier(value: str) -> dict[str, int]:
    match = _PAIR.fullmatch(value)
    if match is None:
        raise UserFacingError("邀请阶梯格式应为“人数:累计增加次数”")
    return {"invite_count": int(match.group(1)), "total_bonus": int(match.group(2))}


def _validate_tiers(tiers: list[dict[str, int]], cap: int) -> None:
    if not tiers or cap < 1:
        raise UserFacingError("邀请加成至少需要一个阶梯，且上限必须大于 0")
    counts = [item["invite_count"] for item in tiers]
    totals = [item["total_bonus"] for item in tiers]
    _validate_invite_counts(counts)
    _validate_invite_totals(totals, cap)


def _validate_invite_counts(counts: list[int]) -> None:
    if min(counts) < 1 or counts != sorted(set(counts)):
        raise UserFacingError("邀请人数必须从小到大且不能重复")
    if counts[-1] > MAX_INVITE_COUNT:
        raise UserFacingError(f"邀请人数不能超过 {MAX_INVITE_COUNT}")


def _validate_invite_totals(totals: list[int], cap: int) -> None:
    if min(totals) < 1 or totals != sorted(set(totals)) or totals[-1] != cap:
        raise UserFacingError("累计增加次数必须递增，且最后一档必须等于总上限")
    if cap > MAX_INVITE_BONUS_TICKETS:
        raise UserFacingError(f"邀请加成总上限不能超过 {MAX_INVITE_BONUS_TICKETS}")


def _reject_duplicate(seen: set[str], key: str, label: str) -> None:
    if key in seen:
        raise UserFacingError(f"{label}加成只能配置一次")
    seen.add(key)
