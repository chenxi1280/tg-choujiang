import re
from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import MAX_CHAT_MESSAGE_THRESHOLD
from tg_lottery.telegram.platform_requirements import platform_summary

ButtonRows = list[list[tuple[str, str]]]
PARTICIPATION_PAGE_SIZE = 10
_PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{5,32})(?:/\d+)?/?(?:\?.*)?")
_PRIVATE_MESSAGE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?(?:\?.*)?")


def parse_chat_refs(value: str) -> list[int | str]:
    refs = [_parse_chat_ref(item) for item in split_values(value)]
    return list(dict.fromkeys(refs))


def message_chat_refs(message: Any) -> list[int | str]:
    forwarded_chat_id = _forwarded_chat_id(message)
    if forwarded_chat_id is not None:
        return [forwarded_chat_id]
    if getattr(message, "forward_origin", None) is not None:
        raise UserFacingError("该转发消息没有携带群聊或频道来源，请改发群内消息链接")
    text = (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()
    if text:
        return parse_chat_refs(text)
    raise UserFacingError("请发送群聊/频道链接或转发来源可见的群聊/频道消息")


def _forwarded_chat_id(message: Any) -> int | None:
    origin = getattr(message, "forward_origin", None)
    for attribute in ("chat", "sender_chat"):
        chat_id = _negative_chat_id(getattr(origin, attribute, None))
        if chat_id is not None:
            return chat_id
    return _negative_chat_id(getattr(message, "forward_from_chat", None))


def _negative_chat_id(chat: Any) -> int | None:
    chat_id = getattr(chat, "id", None)
    if isinstance(chat_id, int) and chat_id < 0:
        return chat_id
    return None


def _parse_chat_ref(value: str) -> int | str:
    if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
        return value
    private = _PRIVATE_MESSAGE_LINK.fullmatch(value)
    if private:
        return int(f"-100{private.group(1)}")
    public = _PUBLIC_LINK.fullmatch(value)
    if public:
        return f"@{public.group(1)}"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if "t.me/+" in value or "t.me/joinchat/" in value:
        raise UserFacingError(
            "私有邀请链接无法识别群信息；若这是主活动群无需添加，"
            "否则请发送 t.me/c/... 消息链接或转发来源可见的频道消息"
        )
    raise UserFacingError("请发送群聊/频道链接；数字 chat ID 仅用于高级操作")


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、\s]+", value) if item.strip()]


def participation_rows(data: dict[str, Any]) -> ButtonRows:
    rows = [[("添加准入条件", "draft_participation_add")]]
    chats = data.get("required_chats") or []
    page = participation_page(data)
    if len(chats) > PARTICIPATION_PAGE_SIZE:
        navigation = []
        if page > 0:
            navigation.append(("← 上一页", "draft_participation_prev"))
        if (page + 1) * PARTICIPATION_PAGE_SIZE < len(chats):
            navigation.append(("下一页 →", "draft_participation_next"))
        rows.append(navigation)
    if chats:
        rows.append(
            [
                ("删除指定项", "draft_participation_delete"),
                ("清空全部", "draft_participation_clear"),
            ]
        )
    start = page * PARTICIPATION_PAGE_SIZE
    if data.get("participation_rules_version") != "v3.0":
        rows.extend(_threshold_rows(data, chats[start : start + PARTICIPATION_PAGE_SIZE]))
    rows.append([("↩ 返回上一层", "draft_back")])
    return rows


def _threshold_rows(data: dict[str, Any], page_chats: list[dict[str, Any]]) -> ButtonRows:
    thresholds = data.get("chat_message_thresholds") or {}
    rows: ButtonRows = []
    for chat in page_chats:
        if chat.get("type") == "channel":
            continue
        chat_id = str(chat["id"])
        current = int(thresholds.get(chat_id, 0))
        label = f"自定义 · {current} 条" if current else "自定义条数"
        rows.append(
            [
                ("发言门槛 −", f"draft_chat_threshold:{chat_id}:dec"),
                (label, f"draft_threshold_input:{chat_id}"),
                ("＋", f"draft_chat_threshold:{chat_id}:inc"),
            ]
        )
    return rows


def validate_main_threshold(data: dict[str, Any], main_chat: dict[str, Any]) -> None:
    thresholds = data.get("chat_message_thresholds") or {}
    if str(main_chat.get("id")) in thresholds and main_chat.get("type") == "channel":
        raise UserFacingError("主活动载体是频道，无法统计发言；请先清零主群发言门槛")


def apply_threshold(data: dict[str, Any], action: str) -> dict[str, Any]:
    reject_v3_legacy_gate(data)
    _, chat_id, op = action.split(":")
    thresholds = apply_threshold_action(
        dict(data.get("chat_message_thresholds") or {}), chat_id, op
    )
    return data | {"chat_message_thresholds": thresholds}


def apply_threshold_action(thresholds: dict[str, int], chat_id: str, op: str) -> dict[str, int]:
    current = int(thresholds.get(chat_id, 0))
    if op == "inc":
        if current >= MAX_CHAT_MESSAGE_THRESHOLD:
            raise UserFacingError(f"发言门槛最高 {MAX_CHAT_MESSAGE_THRESHOLD} 条")
        return thresholds | {chat_id: current + 1}
    if op == "dec":
        if current > 1:
            return thresholds | {chat_id: current - 1}
        return {key: value for key, value in thresholds.items() if key != chat_id}
    return thresholds  # noop 仅刷新显示


def participation_add_text() -> str:
    return (
        "🧭 添加准入条件（必须全部满足）\n\n"
        "点击下方列表勾选群聊或频道，再次点击取消；可搜索名称或刷新列表。\n"
        "完成后点击「返回条件列表」。\n\n"
        "参与活动的群无需在这里重复添加。\n"
        "创建者和机器人都必须是每个目标群聊/频道的管理员。\n"
        "列表中没有目标时，也可直接发送群聊或频道链接，例如：https://t.me/example_group\n"
        "一次可发送一个或多个，用逗号、空格或换行分隔。\n"
        "私有群/频道请发送其中一条消息的 t.me/c/... 链接，"
        "也可转发来源可见的群聊或频道消息；数字 chat ID 仅用于高级操作。"
    )


def _main_chat_line(data: dict[str, Any], thresholds: dict[str, int]) -> str | None:
    if data.get("chat_id") is None:
        return None
    main_threshold = int(thresholds.get(str(data["chat_id"]), 0))
    suffix = f"（发言 ≥ {main_threshold} 条）" if main_threshold else ""
    return f"主活动群：{data.get('group_title') or '未命名群组'}{suffix}"


def participation_text(data: dict[str, Any]) -> str:
    chats = data.get("required_chats") or []
    thresholds = data.get("chat_message_thresholds") or {}
    lines = _participation_intro(data)
    main_line = _main_chat_line(data, thresholds)
    if main_line is not None:
        lines.append(main_line)
    if not chats:
        lines.append("\n当前没有额外条件。")
        return "\n".join(lines)
    page = participation_page(data)
    start = page * PARTICIPATION_PAGE_SIZE
    page_chats = chats[start : start + PARTICIPATION_PAGE_SIZE]
    pages = (len(chats) - 1) // PARTICIPATION_PAGE_SIZE + 1
    lines.append(f"\n当前已验证：共 {len(chats)} 项 · 第 {page + 1}/{pages} 页")
    for index, chat in enumerate(page_chats, start=start + 1):
        privacy = "私有" if chat.get("private") else f"@{chat['username']}"
        action = "关注频道" if chat.get("type") == "channel" else "加入群聊"
        line = f"{index}. {action} {chat['title']} · {privacy}"
        if chat.get("type") != "channel":
            current = int(thresholds.get(str(chat["id"]), 0))
            if current:
                line += f"（发言 ≥ {current} 条）"
        lines.append(line)
    return "\n".join(lines)


def participation_delete_text(data: dict[str, Any]) -> str:
    return participation_text(data) + "\n\n请回复要删除的编号，多个编号用逗号分隔。"


def parse_positions(value: str, count: int) -> tuple[int, ...]:
    parts = split_values(value)
    if not parts or any(not part.isdecimal() for part in parts):
        raise UserFacingError("请填写要删除的数字编号")
    positions = tuple(dict.fromkeys(int(part) for part in parts))
    if any(position < 1 or position > count for position in positions):
        raise UserFacingError(f"删除编号必须在 1–{count} 之间")
    return positions


def participation_page(data: dict[str, Any]) -> int:
    chats = data.get("required_chats") or []
    last_page = max(0, (len(chats) - 1) // PARTICIPATION_PAGE_SIZE)
    return min(max(0, int(data.get("participation_page", 0))), last_page)


def _participation_intro(data: dict[str, Any]) -> list[str]:
    lines = [
        "🧭 准入条件（必须全部满足）",
        platform_summary(data),
        "参与活动的群会另行全部校验。",
        "可一次发送一个或多个群聊/频道链接。",
    ]
    if data.get("folder_link"):
        lines.append("组合分享链接：已设置（仍逐项核验）")
    return lines


def reject_v3_legacy_gate(data):
    if data.get("participation_rules_version") == "v3.0":
        raise UserFacingError("新活动请在抽奖门槛中选择发言数或邀请好友；旧门槛编辑入口已失效")
