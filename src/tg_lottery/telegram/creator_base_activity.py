from typing import Any

from tg_lottery.domain.errors import UserFacingError
from tg_lottery.domain.rules import BaseActivityRule
from tg_lottery.telegram.creator_bonus_permissions import verify_publication_bonus_permissions
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
                f"每条至少 {rule['min_text_length']} 字"
            )
            if rule
            else "未设置"
        )
        lines.append(f"{index}. {group['title']}：{value}")
    lines.append("按北京时间自然日统计；未完成会中断连续天数，完成一次后保留本期达标资格。")
    lines.append("先报名再累计。基础门槛未完成时，加成暂存，有效抽奖次数为 0。")
    return "\n".join(lines)


def apply_base_rule(data: dict[str, Any], text: str) -> dict[str, Any]:
    parts = text.split()
    _validate_parts(parts)
    groups = data.get("participation_groups") or []
    index = int(parts[0]) - 1
    if index < 0 or index >= len(groups):
        raise UserFacingError("群序号无效，请使用页面显示的群序号")
    key = str(groups[index]["id"])
    rules = dict(data.get("base_activity_rules") or {})
    if len(parts) == 2:
        if parts[1] != "0":
            raise UserFacingError("关闭门槛请输入：群序号 0")
        return data | {"base_activity_rules": {k: v for k, v in rules.items() if k != key}}
    if any(int(part) <= 0 for part in parts[1:]):
        raise UserFacingError("天数、条数和字数必须为正整数")
    rule = BaseActivityRule(
        consecutive_days=int(parts[1]), daily_messages=int(parts[2]), min_text_length=int(parts[3])
    )
    return data | {"base_activity_rules": rules | {key: rule.model_dump()}}


class CreatorBaseActivity:
    def __init__(self, *, sessions: Any, renderer: Any, bot: Any) -> None:
        self._bot = bot
        self._sessions = sessions
        self._renderer = renderer

    async def show(self, query: Any, _: object) -> None:
        data = await required_draft_data(self._sessions, query.from_user.id)
        await self._sessions.set(query.from_user.id, "draft:base_activity", data)
        await self._renderer.render(
            query.message,
            user_id=query.from_user.id,
            text=self._text(data),
            rows=[[("完成设置", "draft_back")]],
        )

    async def receive(self, message: Any, data: dict[str, Any]) -> None:
        changed = apply_base_rule(data, message.text or "")
        await verify_publication_bonus_permissions(self._bot, message.from_user.id, changed)
        await self._sessions.set(message.from_user.id, "draft:base_activity", changed)
        await self._renderer.edit(
            PanelTarget(message.from_user.id, message.chat.id, int(data["panel_message_id"])),
            text=self._text(changed),
            rows=[[("完成设置", "draft_back")]],
        )

    def _text(self, data: dict[str, Any]) -> str:
        return (
            base_rules_text(data) + "\n\n发送即保存该群设置，可继续设置其他群。\n"
            "格式：群序号 连续天数 每天条数 最少字数\n"
            "例如：1 7 5 5\n关闭某群门槛：1 0"
        )


def _validate_parts(parts: list[str]) -> None:
    if len(parts) not in (2, 4) or not all(part.isdecimal() for part in parts):
        raise UserFacingError("请输入：群序号 连续天数 每天条数 最少字数；关闭请输入：群序号 0")
