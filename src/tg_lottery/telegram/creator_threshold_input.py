from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import MAX_CHAT_MESSAGE_THRESHOLD
from tg_lottery.telegram.creator_participation import participation_rows, participation_text
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data


class CreatorThresholdInput:
    def __init__(self, *, sessions: Any, renderer: Any) -> None:
        self._sessions = sessions
        self._renderer = renderer

    async def show(self, query: Any, grant: Any) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        chat_id = grant.action.rsplit(":", 1)[-1]
        chat = _required_chat(data, chat_id)
        changed = data | {
            "threshold_input_chat": chat_id,
            "panel_message_id": query.message.message_id,
        }
        await self._sessions.set(query.from_user.id, "draft:chat_threshold", changed)
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text=(
                f"💬 {chat['title']} · 发言准入条件\n\n"
                f"请输入至少需要的发言条数（0–{MAX_CHAT_MESSAGE_THRESHOLD}），0 表示不限制。\n"
                "例如发送 4，表示至少发言 4 条；此项是准入门槛，不是每日加成。"
            ),
            rows=[[("↩ 返回准入条件", "draft_participation")]],
        )

    async def receive(self, message: Any, data: dict[str, Any]) -> None:
        changed = apply_threshold_input(data, message.text or "")
        await self._sessions.set(message.from_user.id, "draft:participation", changed)
        await self._renderer.edit(
            PanelTarget(message.from_user.id, message.chat.id, int(data["panel_message_id"])),
            text=participation_text(changed),
            rows=participation_rows(changed),
        )


def apply_threshold_input(data: dict[str, Any], text: str) -> dict[str, Any]:
    chat_id = str(data["threshold_input_chat"])
    _required_chat(data, chat_id)
    value = text.strip()
    if not value.isdecimal() or not 0 <= int(value) <= MAX_CHAT_MESSAGE_THRESHOLD:
        raise UserFacingError(f"发言条数必须是 0–{MAX_CHAT_MESSAGE_THRESHOLD} 的整数")
    thresholds = dict(data.get("chat_message_thresholds") or {})
    if int(value):
        thresholds = thresholds | {chat_id: int(value)}
    else:
        thresholds = {key: count for key, count in thresholds.items() if key != chat_id}
    return {key: value for key, value in data.items() if key != "threshold_input_chat"} | {
        "chat_message_thresholds": thresholds
    }


def _required_chat(data: dict[str, Any], chat_id: str) -> dict[str, Any]:
    for chat in data.get("required_chats") or []:
        if str(chat["id"]) == chat_id and chat.get("type") != "channel":
            return chat
    raise UserFacingError("该准入群不存在或不能设置发言条件，请返回重新选择")
