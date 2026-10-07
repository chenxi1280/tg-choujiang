from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram import creator_behavior_views as views
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
from tg_lottery.telegram.creator_bonus_policy import uses_unlimited_policy
from tg_lottery.telegram.creator_gate_schedule import validate_gate_time
from tg_lottery.telegram.creator_invite_retention import view as retention_view
from tg_lottery.telegram.creator_renderer import PanelTarget
from tg_lottery.telegram.creator_required_chats_data import required_draft_data
from tg_lottery.telegram.creator_retention_revision import (
    resolve_retention,
    retention_conflict_view,
    synchronize_retention,
)


class CreatorBehaviorAdmission:
    def __init__(self, *, sessions: Any, renderer: Any, bot: Any) -> None:
        self._sessions, self._renderer, self._bot = sessions, renderer, bot

    async def show(self, query: Any, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        opened = data if editor.EDITOR_KEY in data else editor.open_editor(data)
        await self._render(query, synchronize_retention(opened))

    async def handle(self, query: Any, grant: Any) -> bool:
        data = await required_draft_data(self._sessions, query.from_user.id)
        action = grant.action
        if action in {"draft_gate:retention_saved", "draft_gate:retention_edited"}:
            await self._render(query, resolve_retention(data, use_saved=action.endswith("saved")))
            return False
        if action == "draft_gate:leave" and editor.dirty(data):
            await self._unsaved_warning(query, data=data)
            return False
        if action == "draft_gate:continue":
            await self._render(query, data)
            return False
        if action.startswith("draft_gate:retention"):
            await self._retention(query, data, action=action)
            return False
        changed = _apply_action(data, action)
        if action in {"draft_gate:save", "draft_gate:copy_confirm"}:
            gate = (
                changed["admission"] if action == "draft_gate:save" else changed[editor.EDITOR_KEY]
            )
            permission_data = changed | {
                "admission": dict(type=gate["type"], groups=gate["groups"]),
                "invite_retention_hours": gate.get(
                    "retention_hours", changed.get("invite_retention_hours")
                ),
            }
            validate_gate_time(permission_data)
            await verify_publication_bonus_permissions(
                self._bot,
                query.from_user.id,
                permission_data,
            )
        if editor.EDITOR_KEY not in changed:
            await self._sessions.set(query.from_user.id, "draft:idle", changed)
            return True
        await self._render(query, changed)
        return False

    async def receive(self, message: Any, data: dict[str, Any]) -> None:
        changed = editor.receive(data, message.text or "")
        await self._sessions.set(message.from_user.id, "draft:behavior_admission", changed)
        text, rows = views.view(changed)
        await self._renderer.edit(
            PanelTarget(message.from_user.id, message.chat.id, int(changed["panel_message_id"])),
            text=text,
            rows=rows,
        )

    async def _render(self, query: Any, data: dict) -> None:
        updated = synchronize_retention(data) | {"panel_message_id": query.message.message_id}
        await self._sessions.set(query.from_user.id, "draft:behavior_admission", updated)
        text, rows = (
            retention_conflict_view(updated)
            if updated[editor.EDITOR_KEY].get("retention_conflict")
            else views.view(updated)
        )
        await self._renderer.render(query.message, user_id=query.from_user.id, text=text, rows=rows)

    async def _retention(self, query: Any, data: dict, *, action: str) -> None:
        staged = data[editor.EDITOR_KEY]
        if uses_unlimited_policy(data) and action not in {
            "draft_gate:retention",
            "draft_gate:retention:24",
        }:
            raise UserFacingError("新场邀请固定入群满24小时后核验")
        if action == "draft_gate:retention":
            changed = data
        else:
            staged = staged | {
                "retention_hours": int(action.rsplit(":", 1)[-1]),
                "retention_modified": True,
            }
            changed = data | {editor.EDITOR_KEY: staged}
            gate = dict(type=staged["type"], groups=staged["groups"])
            validate_gate_time(
                changed | {"admission": gate, "invite_retention_hours": staged["retention_hours"]}
            )
        await self._sessions.set(query.from_user.id, "draft:behavior_admission", changed)
        text, _ = retention_view(changed | {"invite_retention_hours": staged["retention_hours"]})
        rows = [
            [("立即核验", "draft_gate:retention:0")],
            [("留存24小时", "draft_gate:retention:24"), ("留存48小时", "draft_gate:retention:48")],
            [("返回邀请门槛", "draft_gate:list")],
        ]
        if uses_unlimited_policy(data):
            rows = [[("返回邀请门槛", "draft_gate:list")]]
        await self._renderer.render(query.message, user_id=query.from_user.id, text=text, rows=rows)

    async def _unsaved_warning(self, query, *, data):
        rows = [[("放弃修改", "draft_gate:discard"), ("继续编辑", "draft_gate:continue")]]
        if data[editor.EDITOR_KEY].get("groups") and not editor.group_dirty(data):
            rows.insert(0, [("保存本场门槛并返回预览", "draft_gate:save")])
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text="门槛设置尚未保存，保存后才会替换原规则。",
            rows=rows,
        )


def _apply_action(data, action):
    if action in {"draft_gate:leave", "draft_gate:discard"}:
        return editor.clear(data)
    if action.startswith("draft_gate:copy_toggle:"):
        return editor.toggle_copy(data, action.rsplit(":", 1)[-1])
    return editor.apply_action(data, action)
