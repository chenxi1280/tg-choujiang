from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.prize_limits import require_supported_winner_count
from tg_lottery.telegram.creator_message_context import callback_message, message_user, panel_target
from tg_lottery.telegram.creator_participation import parse_positions
from tg_lottery.telegram.creator_prize_confirmation import clear_confirmation, delete_confirmation
from tg_lottery.telegram.creator_prize_edit import (
    PRIZE_EDIT_KEYS,
    PRIZE_EDIT_STATES,
    prize_edit_input_view,
    prize_edit_view,
    replace_prize_field,
    without_prize_edit,
)
from tg_lottery.telegram.creator_required_chats_data import required_draft_data
from tg_lottery.telegram.creator_schedule import positive_int
from tg_lottery.telegram.creator_sections import enter_section, section_view
from tg_lottery.telegram.keyboard_layout import list_rows

PRIZE_NAME_LIMIT = 120
PRIZE_ACTIONS = frozenset(
    {
        "draft_prizes",
        "draft_add_prize",
        "draft_delete_prize",
        "draft_clear_prizes",
        "draft_clear_prizes_confirm",
        "draft_delete_prize_confirm",
        "draft_prize_cancel",
        "draft_prize_rename",
        "draft_prize_continue",
        "draft_edit_prize",
        "draft_prize_edit_name",
        "draft_prize_edit_count",
        "draft_prize_edit_cancel",
    }
)
PENDING_KEYS = frozenset({"pending_prize_name", "pending_prize_delete"}) | PRIZE_EDIT_KEYS
PRIZE_INPUT_STATES = (
    frozenset({"draft:prize_name", "draft:prize_count", "draft:prize_delete"}) | PRIZE_EDIT_STATES
)


class CreatorPrizes:
    input_states = PRIZE_INPUT_STATES

    def __init__(self, *, sessions: Any, renderer: Any) -> None:
        self._sessions = sessions
        self._renderer = renderer

    def handles_action(self, action):
        return action in PRIZE_ACTIONS or action.startswith("draft_prize_edit:")

    async def handle(self, query, grant) -> None:
        if grant.action.startswith("draft_prize_edit:"):
            await self.select_edit(query, int(grant.action.removeprefix("draft_prize_edit:")))
            return
        if grant.action in {"draft_prize_edit_name", "draft_prize_edit_count"}:
            await self.edit_input(query, grant.action)
            return
        actions = {
            "draft_prizes": self.show,
            "draft_add_prize": self.name,
            "draft_prize_rename": self.name,
            "draft_prize_continue": self.continue_count,
            "draft_prize_cancel": self.cancel,
            "draft_delete_prize": self.delete,
            "draft_clear_prizes": self.confirm_clear,
            "draft_clear_prizes_confirm": self.clear,
            "draft_delete_prize_confirm": self.commit_delete,
            "draft_edit_prize": self.edit_list,
            "draft_prize_edit_cancel": self.cancel_edit,
        }
        await actions[grant.action](query)

    async def show(self, query) -> None:
        data = enter_section(without_prize_edit(await self._data(query)), "prizes")
        text, rows = section_view(data, "prizes")
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def name(self, query) -> None:
        data = enter_section(without_prize_edit(await self._data(query)), "prizes")
        text = "添加奖品 · 名称\n\n请输入奖品名称（最多120字）。\n"
        if data.get("pending_prize_name"):
            text += f"本次名称：{data['pending_prize_name']}\n"
        text += "名称与中奖人数填写完整后才加入奖品清单。"
        await self._render(
            query,
            data=data,
            state="draft:prize_name",
            text=text,
            rows=[[("取消新增奖品", "draft_prize_cancel")]],
        )

    async def receive(self, message, *, state: str, data: dict) -> None:
        handlers = {
            "draft:prize_name": self.receive_name,
            "draft:prize_count": self.receive_count,
            "draft:prize_delete": self.receive_delete,
            "draft:prize_edit_name": self.receive_edit_name,
            "draft:prize_edit_count": self.receive_edit_count,
        }
        await handlers[state](message, data)

    async def receive_name(self, message, data: dict) -> None:
        value = (message.text or "").strip()
        if not 1 <= len(value) <= PRIZE_NAME_LIMIT:
            raise UserFacingError("奖品名称长度必须为1–120字，原有奖品未改变")
        updated = data | {"pending_prize_name": value}
        text = f"添加奖品 · 中奖人数\n\n本次奖品：{value}\n尚未加入清单，请输入正整数人数（人）。"
        rows = [[("重新输入名称", "draft_prize_rename")], [("取消新增奖品", "draft_prize_cancel")]]
        await self._edit(message, data=updated, state="draft:prize_count", text=text, rows=rows)

    async def receive_count(self, message, data: dict) -> None:
        count = positive_int(message.text or "", "中奖人数（人）")
        require_supported_winner_count(
            [count, *(p["winner_count"] for p in data.get("prizes", []))]
        )
        prize = {"name": str(data["pending_prize_name"]), "winner_count": count}
        updated = _clean(data) | {"prizes": [*(data.get("prizes") or []), prize]}
        await self._summary(message, updated)

    async def continue_count(self, query) -> None:
        data = enter_section(await self._data(query), "prizes")
        name = data["pending_prize_name"]
        text = f"添加奖品 · 中奖人数\n\n本次奖品：{name}\n尚未加入清单，请输入正整数人数（人）。"
        rows = [[("重新输入名称", "draft_prize_rename")], [("取消新增奖品", "draft_prize_cancel")]]
        await self._render(query, data=data, state="draft:prize_count", text=text, rows=rows)

    async def cancel(self, query) -> None:
        data = _clean(await self._data(query))
        text, rows = section_view(data, "prizes")
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def delete(self, query) -> None:
        data = without_prize_edit(await self._data(query))
        text, _ = section_view(data, "prizes")
        text += "\n\n请输入要删除的奖品编号；多个编号用逗号分隔。提交后先核对删除范围。"
        await self._render(
            query,
            data=data,
            state="draft:prize_delete",
            text=text,
            rows=[[("返回奖品与领奖", "draft_prizes")]],
        )

    async def receive_delete(self, message, data: dict) -> None:
        positions = parse_positions(message.text or "", len(data.get("prizes") or []))
        updated = data | {"pending_prize_delete": sorted(positions)}
        text, rows = delete_confirmation(updated)
        await self._edit(
            message, data=updated, state="draft:prize_delete_confirm", text=text, rows=rows
        )

    async def commit_delete(self, query) -> None:
        data = await self._data(query)
        selected = frozenset(data["pending_prize_delete"])
        prizes = [p for i, p in enumerate(data["prizes"], 1) if i not in selected]
        await self._save_summary(query, _clean(data) | {"prizes": prizes})

    async def confirm_clear(self, query) -> None:
        data = without_prize_edit(await self._data(query))
        text, rows = clear_confirmation(data)
        await self._render(
            query, data=data, state="draft:prize_clear_confirm", text=text, rows=rows
        )

    async def clear(self, query) -> None:
        await self._save_summary(query, _clean(await self._data(query)) | {"prizes": []})

    async def edit_list(self, query) -> None:
        data = without_prize_edit(await self._data(query))
        rows = list_rows(
            [
                [(prize["name"], f"draft_prize_edit:{index}")]
                for index, prize in enumerate(data["prizes"])
            ]
        )
        rows.append([("返回奖品", "draft_prizes")])
        await self._render(
            query, data=data, state="draft:idle", text="修改奖品\n\n请选择要修改的奖品。", rows=rows
        )

    async def select_edit(self, query, index) -> None:
        data = await self._data(query)
        if not 0 <= index < len(data["prizes"]):
            raise UserFacingError("这项奖品已经变化，请返回奖品列表重新选择")
        updated = _clean(data) | {
            "prize_edit_index": index,
            "prize_edit_original": dict(data["prizes"][index]),
        }
        text, rows = prize_edit_view(updated)
        await self._render(query, data=updated, state="draft:idle", text=text, rows=rows)

    async def edit_input(self, query, action) -> None:
        data = await self._data(query)
        state = "draft:prize_edit_name" if action.endswith("name") else "draft:prize_edit_count"
        text, rows = prize_edit_input_view(data, state)
        await self._render(query, data=data, state=state, text=text, rows=rows)

    async def cancel_edit(self, query) -> None:
        data = await self._data(query)
        text, rows = prize_edit_view(data)
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def receive_edit_name(self, message, data) -> None:
        value = (message.text or "").strip()
        if not 1 <= len(value) <= PRIZE_NAME_LIMIT:
            raise UserFacingError("奖品名称长度必须为1–120字，原有奖品未改变")
        await self._save_edit(message, replace_prize_field(data, field="name", value=value))

    async def receive_edit_count(self, message, data) -> None:
        value = positive_int(message.text or "", "中奖人数（人）")
        await self._save_edit(message, replace_prize_field(data, field="winner_count", value=value))

    async def _save_edit(self, message, data) -> None:
        text, rows = prize_edit_view(data)
        await self._edit(
            message, data=data, state="draft:idle", text="已保存。\n\n" + text, rows=rows
        )

    async def _save_summary(self, query, data: dict) -> None:
        text, rows = section_view(data, "prizes")
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def _summary(self, message, data: dict) -> None:
        text, rows = section_view(data, "prizes")
        await self._edit(message, data=data, state="draft:idle", text=text, rows=rows)

    async def _data(self, query) -> dict:
        return await required_draft_data(self._sessions, query.from_user.id)

    async def _render(self, query, *, data: dict, state: str, text: str, rows: list) -> None:
        updated = data | {"panel_message_id": callback_message(query).message_id}
        await self._sessions.set(query.from_user.id, state, updated)
        await self._renderer.render(
            callback_message(query), user_id=query.from_user.id, text=text, rows=rows
        )

    async def _edit(self, message, *, data: dict, state: str, text: str, rows: list) -> None:
        await self._sessions.set(message_user(message).id, state, data)
        await self._renderer.edit(panel_target(message, data), text=text, rows=rows)


def _clean(data: dict) -> dict:
    return {key: value for key, value in data.items() if key not in PENDING_KEYS}
