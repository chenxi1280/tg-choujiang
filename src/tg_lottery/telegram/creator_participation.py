import re
from typing import Any

from tg_lottery.domain.errors import UserFacingError

ButtonRows = list[list[tuple[str, str]]]


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
    rows = [[("继续添加", "draft_participation_add")]]
    if data.get("required_chats"):
        rows.append(
            [
                ("删除最后一个", "draft_participation_delete"),
                ("清空全部", "draft_participation_clear"),
            ]
        )
    rows.append([("↩ 返回上一层", "draft_back")])
    return rows


def participation_text(data: dict[str, Any]) -> str:
    chats = data.get("required_chats") or []
    lines = ["📮 额外参与条件", "主活动群始终是参与条件。", "请回复一个公开用户名或数字 ID。"]
    if not chats:
        lines.append("\n当前没有额外条件。")
        return "\n".join(lines)
    lines.append("\n当前已验证：")
    for index, chat in enumerate(chats, start=1):
        privacy = "私有" if chat.get("private") else f"@{chat['username']}"
        lines.append(f"{index}. {chat['title']} · {chat['type']} · {privacy}")
    return "\n".join(lines)
