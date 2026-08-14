import re
from typing import Any

from tg_lottery.domain.errors import UserFacingError

ButtonRows = list[list[tuple[str, str]]]
PARTICIPATION_PAGE_SIZE = 10


def parse_chat_refs(value: str) -> list[int | str]:
    refs: list[int | str] = []
    for item in split_values(value):
        if re.fullmatch(r"@[A-Za-z0-9_]{5,32}", item):
            refs.append(item)
            continue
        try:
            refs.append(int(item))
        except ValueError as exc:
            raise UserFacingError("群/频道需填写公开用户名或数字 ID") from exc
    return list(dict.fromkeys(refs))


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
        "请回复要添加的群聊/频道的公开用户名或数字 ID。\n"
        "一次可填写一个或多个，用逗号、空格或换行分隔。"
    )


def participation_text(data: dict[str, Any]) -> str:
    chats = data.get("required_chats") or []
    lines = [
        "📮 参与条件",
        "主活动群始终是参与条件。",
        "可一次回复一个或多个群聊/频道的公开用户名或数字 ID。",
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
