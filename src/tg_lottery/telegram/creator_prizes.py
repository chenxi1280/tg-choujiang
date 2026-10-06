from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.creator_message_context import callback_message, message_user, panel_target
from tg_lottery.telegram.creator_participation import parse_positions
from tg_lottery.telegram.creator_required_chats_data import required_draft_data
from tg_lottery.telegram.creator_schedule import positive_int
from tg_lottery.telegram.creator_sections import enter_section, section_view

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
    }
)
PENDING_KEYS = frozenset({"pending_prize_name", "pending_prize_delete"})


class CreatorPrizes:
    def __init__(self, *, sessions: Any, renderer: Any) -> None:
        self._sessions = sessions
        self._renderer = renderer

    async def handle(self, query, grant) -> None:
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
        }
        await actions[grant.action](query)

    async def show(self, query) -> None:
        data = enter_section(await self._data(query), "prizes")
        text, rows = section_view(data, "prizes")
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def name(self, query) -> None:
        data = enter_section(await self._data(query), "prizes")
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
        data = await self._data(query)
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
        names = [p["name"] for i, p in enumerate(data["prizes"], 1) if i in positions]
        text = "确认删除奖品\n\n将删除：" + "、".join(names) + "\n其他奖品不变。"
        rows = [
            [("确认删除指定奖品", "draft_delete_prize_confirm")],
            [("保留奖品", "draft_prizes")],
        ]
        await self._edit(message, data=updated, state="draft:idle", text=text, rows=rows)

    async def commit_delete(self, query) -> None:
        data = await self._data(query)
        selected = frozenset(data["pending_prize_delete"])
        prizes = [p for i, p in enumerate(data["prizes"], 1) if i not in selected]
        await self._save_summary(query, _clean(data) | {"prizes": prizes})

    async def confirm_clear(self, query) -> None:
        data = await self._data(query)
        text = (
            f"确认清空奖品\n\n将移除本草稿的{len(data.get('prizes') or [])}项奖品，其他配置保留。"
        )
        rows = [[("确认清空奖品", "draft_clear_prizes_confirm")], [("保留奖品", "draft_prizes")]]
        await self._render(query, data=data, state="draft:idle", text=text, rows=rows)

    async def clear(self, query) -> None:
        await self._save_summary(query, _clean(await self._data(query)) | {"prizes": []})

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
