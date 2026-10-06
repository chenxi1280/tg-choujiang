from typing import Any

from tg_lottery.telegram import creator_base_editor as editor
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
from tg_lottery.telegram.creator_participation import reject_v3_legacy_gate
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data


def base_rules_text(data: dict[str, Any]) -> str:
    groups = data.get("participation_groups") or []
    rules = data.get("base_activity_rules") or {}
    lines = ["基础抽奖门槛（各配置群分别达标）"]
    for index, group in enumerate(groups, 1):
        rule = rules.get(str(group["id"]))
        value = (
            (
                f"连续 {rule['consecutive_days']} 天，每天 {rule['daily_messages']} 条，"
                "文字、图片、表情均计数"
            )
            if rule
            else "未设置"
        )
        lines.append(f"{index}. {group['title']}：{value}")
    lines.append("按北京时间自然日统计；未完成会中断连续天数，完成一次后保留本期达标资格。")
    lines.append("先报名再累计。基础门槛未完成时，加成暂存，有效抽奖次数为 0。")
    return "\n".join(lines)


class CreatorBaseActivity:
    def __init__(self, *, sessions: Any, renderer: Any, bot: Any) -> None:
        self._bot = bot
        self._sessions = sessions
        self._renderer = renderer

    async def show(self, query: Any, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        reject_v3_legacy_gate(data)
        await self._render(query, editor.clear_editor(data))

    async def handle(self, query: Any, grant: Any) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        reject_v3_legacy_gate(data)
        changed = editor.apply_action(data, grant.action)
        if grant.action == "draft_base:save":
            await verify_publication_bonus_permissions(self._bot, query.from_user.id, changed)
        await self._render(query, changed)

    async def _render(self, query: Any, data: dict[str, Any]) -> None:
        updated = data | {"panel_message_id": query.message.message_id}
        await self._sessions.set(query.from_user.id, "draft:base_activity", updated)
        text, rows = self._view(updated)
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text=text,
            rows=rows,
        )

    async def receive(self, message: Any, data: dict[str, Any]) -> None:
        reject_v3_legacy_gate(data)
        changed = editor.receive_value(data, message.text or "")
        await self._sessions.set(message.from_user.id, "draft:base_activity", changed)
        text, rows = self._view(changed)
        await self._renderer.edit(
            PanelTarget(message.from_user.id, message.chat.id, int(data["panel_message_id"])),
            text=text,
            rows=rows,
        )

    def _view(self, data: dict[str, Any]) -> tuple[str, list]:
        if data.get(editor.EDITOR_KEY):
            return editor.editor_view(data)
        hint = (
            "请选择要设置的群：" if data.get("participation_groups") else "请先选择参与活动的群。"
        )
        return base_rules_text(data) + "\n\n" + hint, editor.list_rows(data)
