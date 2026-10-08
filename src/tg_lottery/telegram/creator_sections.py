from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_behavior_views import admission_summary
from tg_lottery.telegram.creator_bonus import bonus_summary
from tg_lottery.telegram.creator_bonus_menu import bonus_menu_view
from tg_lottery.telegram.creator_group_bonus import list_view
from tg_lottery.telegram.creator_messages import message_settings_rows, message_settings_text
from tg_lottery.telegram.creator_participation import participation_rows, participation_text
from tg_lottery.telegram.platform_requirements import platform_summary

ButtonRows = list[list[tuple[str, str]]]
SECTION_ACTIONS = {
    "draft_conditions": "conditions",
    "draft_bonus_menu": "bonus",
    "draft_rules": "bonus",
    "draft_cover_summary": "cover",
    "draft_more": "more",
    "draft_prizes": "prizes",
}


def enter_section(data: dict, section: str) -> dict:
    path = tuple(data.get("creator_path") or ("dashboard",))
    updated = path[: path.index(section) + 1] if section in path else (*path, section)
    return data | {"creator_path": list(updated)}


def current_section(data: dict) -> str:
    return str((data.get("creator_path") or ["dashboard"])[-1])


def section_view(data: dict[str, Any], section: str) -> tuple[str, ButtonRows]:
    views = {
        "conditions": _conditions,
        "bonus": _bonus,
        "cover": _cover,
        "more": _more,
        "prizes": _prizes,
        "user_limits": _user_limits,
        "messages": _messages,
        "requirements": lambda draft: (participation_text(draft), participation_rows(draft)),
    }
    if section in {"bonus_invite", "bonus_activity"}:
        return list_view(data, section.removeprefix("bonus_"))
    view = views.get(section)
    if view is None:
        raise UserFacingError("创建页面不存在，请回到当前草稿")
    return view(data)


def next_draft_action(data: dict) -> tuple[str, str]:
    requirements = (
        (
            bool(data.get("participation_groups") or data.get("chat_id")),
            "选择参与群",
            "draft_group",
        ),
        (bool(data.get("title")), "设置活动标题", "draft_title"),
        (bool(data.get("prizes")), "添加奖品", "draft_add_prize"),
        (_gate_complete(data), "设置抽奖门槛", "draft_gate"),
        (
            _valid_draw_time(data),
            "调整开奖时间" if data.get("draw_at") else "设置开奖时间",
            "draft_draw",
        ),
    )
    for complete, label, action in requirements:
        if not complete:
            if action == "draft_add_prize" and data.get("pending_prize_name"):
                return "继续新增奖品", "draft_prize_continue"
            return label, action
    return "预览并发布", "draft_publish"


def _gate_complete(data: dict) -> bool:
    if data.get("participation_rules_version") != "v3.0":
        return True
    gate = data.get("admission") or {}
    return gate.get("type") in {"message", "invite"} and bool(gate.get("groups"))


def _conditions(data: dict) -> tuple[str, ButtonRows]:
    text = (
        "🧭 参与条件设置\n\n全部参与群都必须加入；额外关注、用户限制和行为门槛分别核验。\n"
        f"{platform_summary(data)}"
        f"{admission_summary(data)}"
    )
    rows = [
        [("额外关注/加入要求", "draft_participation"), ("用户限制", "draft_user_limits")],
        [("抽奖门槛", "draft_gate"), ("修改参与群", "draft_group")],
        [("返回草稿", "draft_back")],
    ]
    return text, rows


def _bonus(data: dict) -> tuple[str, ButtonRows]:
    return bonus_menu_view(data, summary=bonus_summary(data))


def _cover(data: dict) -> tuple[str, ButtonRows]:
    has_cover = bool(data.get("cover_file_id"))
    rows = [
        [
            ("替换封面" if has_cover else "上传封面", "draft_cover"),
            ("抽奖说明", "draft_description"),
        ],
    ]
    if has_cover:
        rows.append([("清除封面", "draft_cover_clear")])
    rows.append([("返回草稿", "draft_back")])
    return (
        f"📷 封面与说明\n\n封面：{'已设置' if has_cover else '未设置（可选）'}\n"
        f"抽奖说明：{data.get('description') or '未设置（可选）'}",
        rows,
    )


def _more(data: dict) -> tuple[str, ButtonRows]:
    return (
        "🪪 更多设置\n\n普通设置立即保存；载入和清空会先显示影响并要求确认。\n"
        f"发奖人员：{data.get('issuer_name') or data.get('creator_name') or '当前管理员'}",
        [
            [("发奖人员", "draft_issuer"), ("消息提示", "draft_messages")],
            [("载入上次抽奖配置", "draft_load_last")],
            [("清空并新建草稿", "draft_clear")],
            [("返回草稿", "draft_back")],
        ],
    )


def _prizes(data: dict) -> tuple[str, ButtonRows]:
    prizes = data.get("prizes") or []
    lines = ["📦 奖品与领奖", ""]
    lines.extend(
        f"{index}. {p['name']} × {p['winner_count']} 人" for index, p in enumerate(prizes, 1)
    )
    if not prizes:
        lines.append("尚未添加完整奖品。")
    lines.append(f"\n领奖说明：{data.get('claim_instructions') or '请联系活动管理员领取奖品'}")
    rows = [[("添加奖品", "draft_add_prize"), ("领奖说明", "draft_claim")]]
    if data.get("pending_prize_name"):
        lines.append(f"\n未完成新增：{data['pending_prize_name']}（还需中奖人数）")
        rows = [[("继续新增奖品", "draft_prize_continue"), ("取消新增奖品", "draft_prize_cancel")]]
    if prizes:
        rows.extend(
            [[("删除指定奖品", "draft_delete_prize")], [("清空奖品", "draft_clear_prizes")]]
        )
    if data.get("pending_prize_name"):
        rows.append([("领奖说明", "draft_claim")])
    rows.append([("返回草稿", "draft_back")])
    return "\n".join(lines), rows


def _user_limits(data: dict) -> tuple[str, ButtonRows]:
    required = bool(data.get("username_required"))
    regions = "、".join(data.get("allowed_regions") or []) or "全国"
    return (
        f"🪪 用户限制\n\n用户名：{'必填' if required else '可为空'}\n"
        f"允许地区：{regions}\n选择立即保存。",
        [
            [
                ("✓ 用户名必填" if required else "用户名必填", "draft_username_on"),
                ("用户名可为空" if required else "✓ 用户名可为空", "draft_username_off"),
            ],
            [("设置地区限制", "draft_regions")],
            [("返回参与条件", "draft_back")],
        ],
    )


def _messages(data: dict) -> tuple[str, ButtonRows]:
    return message_settings_text(data), message_settings_rows(data)


def _valid_draw_time(data):
    if not data.get("draw_at"):
        return False
    from datetime import datetime

    from tg_lottery.application.records import utcnow
    from tg_lottery.telegram.creator_schedule import validate_draw_time

    try:
        validate_draw_time(datetime.fromisoformat(str(data["draw_at"])), now=utcnow())
    except (ValueError, UserFacingError):
        return False
    return True
