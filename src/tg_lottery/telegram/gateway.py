import base64
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway_requests import GatewayRequest
from tg_lottery.telegram.public_projection import format_public_time


@dataclass(frozen=True)
class GatewayResult:
    remote_result: dict[str, Any]
    remote_message_id: int | None = None
    invite_link: str | None = None


class TelegramGateway:
    def __init__(self, bot: Bot, cipher: SecretCipher) -> None:
        self._bot = bot
        self._cipher = cipher

    async def execute(
        self,
        outbox: GatewayRequest,
        invite_link: InviteLink | None = None,
    ) -> GatewayResult:
        handlers = {
            "send_campaign_card": self._send_campaign_card,
            "edit_campaign_card_cancelled": self._edit_campaign_card_cancelled,
            "edit_campaign_card_state": self._edit_campaign_card_state,
            "create_invite_link": self._create_invite_link,
            "create_required_chat_invite": self._create_required_chat_invite,
            "send_invite_link": self._send_invite_link,
            "revoke_invite_link": self._revoke_invite_link,
            "revoke_required_chat_invite": self._revoke_required_chat_invite,
            "send_draw_result": self._send_draw_result,
            "send_winner_notice": self._send_winner_notice,
            "delete_message": self._delete_message,
        }
        handler = handlers.get(outbox.operation)
        if handler is None:
            raise ValueError(f"unsupported Telegram outbox operation: {outbox.operation}")
        return await handler(outbox, invite_link)

    async def _send_campaign_card(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        keyboard = _campaign_keyboard(payload)
        text = _campaign_card_text(payload)
        cover = payload.get("presentation", {}).get("cover_file_id")
        if cover:
            message = await self._bot.send_photo(
                outbox.recipient_chat_id, cover, caption=text, reply_markup=keyboard
            )
        else:
            message = await self._bot.send_message(
                outbox.recipient_chat_id, text, reply_markup=keyboard
            )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _edit_campaign_card_cancelled(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        message_id = int(payload["message_id"])
        text = f"⛔ {payload['campaign_name']}\n\n活动已取消，报名入口已关闭。"
        if payload.get("has_cover"):
            result = await self._bot.edit_message_caption(
                caption=text, chat_id=outbox.recipient_chat_id, message_id=message_id
            )
        else:
            result = await self._bot.edit_message_text(
                text, chat_id=outbox.recipient_chat_id, message_id=message_id
            )
        remote_result = (
            result.model_dump(mode="json", exclude_none=True)
            if hasattr(result, "model_dump")
            else {"ok": bool(result)}
        )
        return GatewayResult(remote_result=remote_result, remote_message_id=message_id)

    async def _edit_campaign_card_state(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        message_id = int(payload["message_id"])
        text = _campaign_state_card_text(payload)
        if payload.get("has_cover"):
            result = await self._bot.edit_message_caption(
                caption=text, chat_id=outbox.recipient_chat_id, message_id=message_id
            )
        else:
            result = await self._bot.edit_message_text(
                text, chat_id=outbox.recipient_chat_id, message_id=message_id
            )
        remote = result.model_dump(mode="json") if hasattr(result, "model_dump") else {"ok": True}
        return GatewayResult(remote_result=remote, remote_message_id=message_id)

    async def _create_invite_link(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        link = await self._bot.create_chat_invite_link(
            chat_id=outbox.recipient_chat_id,
            name=str(payload["name"]),
            expire_date=(
                None
                if payload.get("persistent")
                else datetime.fromisoformat(str(payload["expire_at"]))
            ),
            creates_join_request=False,
        )
        remote_result = link.model_dump(mode="json", exclude_none=True)
        remote_result.pop("invite_link", None)
        return GatewayResult(
            remote_result=remote_result,
            invite_link=link.invite_link,
        )

    async def _create_required_chat_invite(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        link = await self._bot.create_chat_invite_link(
            chat_id=outbox.recipient_chat_id,
            name=str(payload["name"]),
            expire_date=datetime.fromisoformat(str(payload["expire_at"])),
            creates_join_request=False,
        )
        remote_result = link.model_dump(mode="json", exclude_none=True)
        remote_result.pop("invite_link", None)
        return GatewayResult(remote_result=remote_result, invite_link=link.invite_link)

    async def _send_invite_link(
        self, outbox: GatewayRequest, invite_link: InviteLink | None
    ) -> GatewayResult:
        if invite_link is None or invite_link.encrypted_url is None:
            raise ValueError("send_invite_link requires a stored encrypted invite link")
        link = self._cipher.decrypt(
            invite_link.encrypted_url,
            context=f"invite-link:{invite_link.id}",
        )
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            "你的本期专属邀请链接:\n" + link,
            disable_web_page_preview=True,
        )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _revoke_invite_link(
        self, outbox: GatewayRequest, invite_link: InviteLink | None
    ) -> GatewayResult:
        if invite_link is None or invite_link.encrypted_url is None:
            raise ValueError("revoke_invite_link requires a stored encrypted invite link")
        link = self._cipher.decrypt(
            invite_link.encrypted_url,
            context=f"invite-link:{invite_link.id}",
        )
        result = await self._bot.revoke_chat_invite_link(outbox.recipient_chat_id, link)
        return GatewayResult(remote_result=result.model_dump(mode="json", exclude_none=True))

    async def _revoke_required_chat_invite(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        encrypted = base64.urlsafe_b64decode(str(payload["join_url_encrypted"]).encode())
        link = self._cipher.decrypt(
            encrypted,
            context=(f"required-chat:{payload['campaign_id']}:{int(payload['chat_id'])}"),
        )
        result = await self._bot.revoke_chat_invite_link(outbox.recipient_chat_id, link)
        return GatewayResult(remote_result=result.model_dump(mode="json", exclude_none=True))

    async def _send_draw_result(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        message = await self._bot.send_message(
            outbox.recipient_chat_id, _draw_result_text(outbox.payload)
        )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _send_winner_notice(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        text = (
            f"恭喜你在「{payload['campaign_name']}」中获得: {payload['prize_name']}"
            f"（名额 {payload['prize_slot_no']}）。\n领奖方式: {payload['claim_instructions']}"
        )
        if payload.get("extended_tips"):
            text += f"\n发奖人员: {payload['issuer_name']}"
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            text,
        )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _delete_message(self, outbox: GatewayRequest, _: InviteLink | None) -> GatewayResult:
        result = await self._bot.delete_message(
            outbox.recipient_chat_id, int(outbox.payload["message_id"])
        )
        return GatewayResult(remote_result={"ok": bool(result)})


def _campaign_deep_link(payload: dict[str, Any], action: str) -> str:
    return f"https://t.me/{payload['bot_username']}?start={action}_{payload['campaign_ref']}"


def _campaign_keyboard(payload: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="立即报名", url=_campaign_deep_link(payload, "join"))],
        [InlineKeyboardButton(text="查看活动规则", url=_campaign_deep_link(payload, "rules"))],
    ]
    conditions = _campaign_conditions(payload)
    if conditions:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"查看参与条件（{len(conditions)}项）",
                    url=_condition_deep_link(payload, 1),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _campaign_conditions(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    presentation = payload.get("presentation", {})
    main = presentation.get("main_chat")
    extras = presentation.get("required_chats") or []
    values = ([main] if isinstance(main, dict) else []) + list(extras)
    return tuple(item for item in values if isinstance(item, dict))


def _condition_deep_link(payload: dict[str, Any], position: int) -> str:
    return (
        f"https://t.me/{payload['bot_username']}?start="
        f"condition_{payload['campaign_ref']}_{position}"
    )


def _campaign_card_text(payload: dict[str, Any]) -> str:
    timezone = payload.get("timezone")
    if not timezone:
        raise ValueError("campaign card timezone is required")
    cutoff = format_public_time(str(payload["cutoff_at"]), str(timezone))
    draw = format_public_time(str(payload["draw_at"]), str(timezone))
    return (
        f"🎁 {payload['name']}\n\n"
        f"中奖名额（共 {payload['total_winners']} 人）:\n{_prize_lines(payload)}\n\n"
        f"报名截止: {cutoff}\n"
        f"开奖时间: {draw}\n\n"
        f"{_campaign_draw_mode(payload)}\n"
        f"发奖人员: {_campaign_issuer(payload)}\n"
        f"参与提示: {_campaign_message_policy(payload)}\n"
        f"{_campaign_requirements(payload)}\n"
        f"{_campaign_description(payload)}"
    )


def _draw_result_text(payload: dict[str, Any]) -> str:
    lines = [f"🏆 {payload['campaign_name']} 开奖结果"]
    _append_winners(lines, payload.get("winners", []))
    _append_undistributed(lines, payload.get("undistributed", []))
    if payload.get("extended_tips"):
        lines.append("本次结果按开奖时冻结的报名资格和抽奖次数权重计算。")
    return "\n".join(lines)


def _append_winners(lines: list[str], winners: list[dict[str, Any]]) -> None:
    if not winners:
        lines.append("本期没有符合条件的中奖候选人。")
        return
    for item in winners:
        lines.append(
            f"• 第{item['prize_position']}顺位 {item['prize_name']}"
            f"（名额 {item['prize_slot_no']}）: {item['display_name']}"
        )


def _append_undistributed(lines: list[str], items: list[dict[str, Any]]) -> None:
    if not items:
        return
    lines.append("未分配名额:")
    for item in items:
        lines.append(f"• 第{item['prize_position']}顺位 {item['prize_name']}: {item['count']} 人")


def _prize_lines(payload: dict[str, Any]) -> str:
    prizes = payload.get("prizes", [])
    return "\n".join(
        f"• 第{item['position']}顺位 {item['name']}：中奖 {item['winner_count']} 人"
        for item in prizes
    )


def _campaign_requirements(payload: dict[str, Any]) -> str:
    rules = payload.get("rules", {})
    username = "用户名必填" if rules.get("username_required") else "用户名可为空"
    regions = rules.get("allowed_regions") or []
    region_text = "、".join(regions) if regions else "全国"
    conditions = _campaign_conditions(payload)
    lines = [f"参与条件: {username}; 地区 {region_text}"]
    if len(conditions) > 3:
        lines.append(f"群/频道: 共 {len(conditions)} 项（请点击逐项查看）")
    else:
        lines.extend(_condition_requirement_lines(conditions, rules))
    activity = _activity_requirement(rules)
    if activity:
        lines.append(activity)
    return "\n".join(lines)


def _condition_requirement_lines(
    conditions: tuple[dict[str, Any], ...], rules: dict[str, Any]
) -> list[str]:
    if not conditions:
        return ["群/频道: 无额外要求"]
    threshold = int((rules.get("activity") or {}).get("message_threshold", 10))
    activity_enabled = _activity_enabled(rules)
    lines = []
    for chat in conditions:
        if chat.get("type") == "channel":
            lines.append(f"• 关注频道 {chat['title']}")
        elif activity_enabled:
            lines.append(f"• 加入群聊 {chat['title']}, 每天有效发言 {threshold} 条")
        else:
            lines.append(f"• 加入群聊 {chat['title']}")
    return lines


def _activity_requirement(rules: dict[str, Any]) -> str | None:
    if not _activity_enabled(rules):
        return None
    activity = rules.get("activity") or {}
    reward = int(activity.get("reward_tickets", 1))
    return f"发言加成: 全部条件群达标后, 当日增加 {reward} 次"


def _activity_enabled(rules: dict[str, Any]) -> bool:
    bonus_types = rules.get("bonus_types")
    if bonus_types is None:
        return rules.get("luck_mode") != "off"
    return "activity" in bonus_types


def _campaign_description(payload: dict[str, Any]) -> str:
    description = payload.get("presentation", {}).get("description")
    if description:
        return f"抽奖说明: {description}"
    rules = payload.get("rules", {})
    bonus_types = rules.get("bonus_types")
    if bonus_types is None:
        enabled = rules.get("luck_mode") != "off"
        bonus_types = ["invite", "activity"] if enabled else []
    if not bonus_types:
        return "每位合格报名者获得 1 次基础抽奖机会。"
    return "报名后可通过已配置的邀请或每日发言增加抽奖次数。"


def _campaign_draw_mode(payload: dict[str, Any]) -> str:
    rules = payload.get("rules", {})
    if rules.get("draw_mode") == "full":
        return f"开奖方式: 满 {rules['participant_target']} 人启动安全结算（30 天兜底）"
    return "开奖方式: 定时开奖"


def _campaign_issuer(payload: dict[str, Any]) -> str:
    presentation = payload.get("presentation", {})
    return str(presentation.get("issuer_name") or "活动创建者")


def _campaign_message_policy(payload: dict[str, Any]) -> str:
    presentation = payload.get("presentation", {})
    trigger = "删除报名命令" if presentation.get("delete_trigger_message") else "保留报名命令"
    seconds = presentation.get("completion_delete_after_seconds")
    completion = "不自动删除" if seconds is None else f"{int(seconds)} 秒后删除"
    return f"{trigger}; 完成提示 {completion}"


def _campaign_state_card_text(payload: dict[str, Any]) -> str:
    state = str(payload["card_state"])
    status = {
        "settling": "报名已截止，正在复核资格并结算抽奖次数。",
        "drawn": "本期已经开奖，报名入口已关闭，请查看最新开奖结果。",
    }.get(state)
    if status is None:
        raise ValueError("unsupported campaign card state")
    return f"🎁 {payload['campaign_name']}\n\n{status}"
