import re
from typing import Any

from tg_lottery.domain.errors import UserFacingError

ButtonRows = list[list[tuple[str, str]]]
PARTICIPATION_PAGE_SIZE = 10
_PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{5,32})(?:/\d+)?/?(?:\?.*)?")
_PRIVATE_MESSAGE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)(?:/\d+)?/?(?:\?.*)?")


def parse_chat_refs(value: str) -> list[int | str]:
    refs = [_parse_chat_ref(item) for item in split_values(value)]
    return list(dict.fromkeys(refs))


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
        raise UserFacingError("私有邀请链接无法识别群信息，请发送该群/频道内的消息链接")
    raise UserFacingError("请发送群聊/频道链接；数字 chat ID 仅用于高级操作")


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、\s]+", value) if item.strip()]


def participation_rows(data: dict[str, Any]) -> ButtonRows:
    rows = [[("添加参与条件", "draft_participation_add")]]
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
    rows.append([("↩ 返回上一层", "draft_back")])
    return rows


def participation_add_text() -> str:
    return (
        "📮 添加参与条件\n\n"
        "请直接发送群聊或频道链接，例如：https://t.me/example_group\n"
        "一次可发送一个或多个，用逗号、空格或换行分隔。\n"
        "私有群/频道请发送其中一条消息的 t.me/c/... 链接；数字 chat ID 仅用于高级操作。"
    )


def participation_text(data: dict[str, Any]) -> str:
    chats = data.get("required_chats") or []
    lines = [
        "📮 参与条件",
        "主活动群始终是参与条件。",
        "可一次发送一个或多个群聊/频道链接。",
    ]
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
        lines.append(f"{index}. {action} {chat['title']} · {privacy}")
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
