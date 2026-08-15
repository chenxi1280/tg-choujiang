import base64
import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway_requests import GatewayRequest

_LOGGER = logging.getLogger(__name__)


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
                outbox.recipient_chat_id,
                cover,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            message = await self._bot.send_message(
                outbox.recipient_chat_id,
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        try:
            await self._bot.pin_chat_message(
                outbox.recipient_chat_id, message.message_id, disable_notification=True
            )
        except TelegramAPIError as exc:
            _LOGGER.warning(
                "campaign card pin rejected",
                extra={"chat_id": outbox.recipient_chat_id, "error": str(exc)},
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
        name = html.escape(str(payload["campaign_name"]))
        text = f"🎰 <b>{name}</b>\n\n活动已取消，报名入口已关闭。"
        if payload.get("has_cover"):
            result = await self._bot.edit_message_caption(
                caption=text,
                chat_id=outbox.recipient_chat_id,
                message_id=message_id,
                parse_mode="HTML",
            )
        else:
            result = await self._bot.edit_message_text(
                text,
                chat_id=outbox.recipient_chat_id,
                message_id=message_id,
                parse_mode="HTML",
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
                caption=text,
                chat_id=outbox.recipient_chat_id,
                message_id=message_id,
                parse_mode="HTML",
            )
        else:
            result = await self._bot.edit_message_text(
                text,
                chat_id=outbox.recipient_chat_id,
                message_id=message_id,
                parse_mode="HTML",
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
    rules = payload.get("rules") or {}
    ref = str(payload["campaign_ref"])
    rows = [[InlineKeyboardButton(text="🎫 立即参与", callback_data=f"member:join:{ref}")]]
    if _invite_enabled(rules):
        rows.append(
            [InlineKeyboardButton(text="🔋 获取加成链接", callback_data=f"member:bonus:{ref}")]
        )
    rows.append([InlineKeyboardButton(text="📜 活动规则", callback_data=f"member:rules:{ref}")])
    conditions = _campaign_conditions(payload)
    if any(not item.get("join_url") for item in conditions):
        rows.append(
            [InlineKeyboardButton(text="查看参与条件入口", url=_condition_deep_link(payload, 1))]
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
    lines = [f"🎰 <b>{html.escape(str(payload['name']))}</b>", "", "📮 <b>参与条件：</b>"]
    lines.extend(_requirement_lines(payload))
    lines += ["", "🎁 <b>奖品内容：</b>"]
    lines += [
        f"💰️ {html.escape(str(item['name']))} × <b>{item['winner_count']}</b>"
        for item in payload.get("prizes", [])
    ]
    description = payload.get("presentation", {}).get("description")
    if description:
        lines += ["", "⚠️ <b>抽奖说明：</b>", html.escape(str(description))]
    bonus = _bonus_line(payload)
    if bonus:
        lines += ["", f"🔋 <b>抽奖加成：</b>{bonus}"]
    lines += ["", *_draw_lines(payload)]
    issuer = _campaign_issuer(payload)
    if issuer != "活动创建者":
        lines += ["", f"🎁 发奖人员：{html.escape(issuer)}"]
    return "\n".join(lines)


def _requirement_lines(payload: dict[str, Any]) -> list[str]:
    rules = payload.get("rules", {})
    lines = []
    regions = rules.get("allowed_regions") or []
    if regions:
        lines.append("🌏️ 归属地：" + "、".join(str(region) for region in regions))
    if rules.get("username_required"):
        lines.append("🪪 必须设置用户名")
    thresholds = {int(k): int(v) for k, v in (rules.get("message_thresholds") or {}).items()}
    for chat in _campaign_conditions(payload):
        lines.extend(_chat_requirement_lines(chat, thresholds))
    return lines or ["（无额外条件）"]


def _chat_requirement_lines(chat: dict[str, Any], thresholds: dict[int, int]) -> list[str]:
    label = html.escape(str(chat.get("title") or "群聊/频道"))
    url = chat.get("join_url")
    verb = "关注" if chat.get("type") == "channel" else "加入"
    if url:
        safe_url = html.escape(str(url), quote=True)
        lines = [f'🎫 {verb}-<a href="{safe_url}">{label}</a>']
    else:
        lines = [f"🎫 {verb}-{label}（入口见下方按钮）"]
    need = thresholds.get(int(chat["id"]))
    if need and chat.get("type") != "channel":
        lines.append(f"└ 🎟️ 发言数大于 <b>{need}</b> 条")
    return lines


def _draw_time(payload: dict[str, Any]) -> str:
    zone = ZoneInfo(str(payload["timezone"]))
    moment = datetime.fromisoformat(str(payload["draw_at"]))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    else:
        moment = moment.astimezone(zone)
    return moment.strftime("%Y年%m月%d日 %H时%M分%S秒")


def _bonus_line(payload: dict[str, Any]) -> str:
    rules = payload.get("rules", {})
    bonus_types = rules.get("bonus_types") or ()
    parts = []
    if "invite" in bonus_types:
        parts.append(_invite_bonus_text(rules.get("invite") or {}))
    if "activity" in bonus_types:
        parts.append(_activity_bonus_text(rules.get("activity") or {}))
    return "；".join(parts)


def _invite_bonus_text(invite: dict[str, Any]) -> str:
    tiers = ", ".join(
        f"{tier['invite_count']}人+{tier['total_bonus']}次"
        for tier in invite.get("reward_tiers") or []
    )
    base = "邀请 " + tiers if tiers else "每个有效邀请+1次"
    cap = invite.get("max_bonus_tickets")
    return base + (f"（上限{cap}）" if cap else "")


def _activity_bonus_text(activity: dict[str, Any]) -> str:
    threshold = activity.get("message_threshold", 10)
    reward = activity.get("reward_tickets", 1)
    return f"每日发言满{threshold}条+{reward}次"


def _draw_lines(payload: dict[str, Any]) -> list[str]:
    rules = payload.get("rules", {})
    if rules.get("draw_mode") == "full":
        return ["🎊 <b>满人开奖：</b>", f"达到 {rules['participant_target']} 人参与后立刻开奖"]
    return ["📅 <b>开奖日期：</b>（北京时间）", _draw_time(payload)]


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


def _campaign_issuer(payload: dict[str, Any]) -> str:
    presentation = payload.get("presentation", {})
    return str(presentation.get("issuer_name") or "活动创建者")


def _invite_enabled(rules: dict[str, Any]) -> bool:
    bonus_types = rules.get("bonus_types")
    if bonus_types is None:
        return rules.get("luck_mode") != "off"
    return "invite" in bonus_types


def _campaign_state_card_text(payload: dict[str, Any]) -> str:
    state = str(payload["card_state"])
    status = {
        "settling": "报名已截止，正在复核资格并结算抽奖次数。",
        "drawn": "本期已经开奖，报名入口已关闭，请查看最新开奖结果。",
    }.get(state)
    if status is None:
        raise ValueError("unsupported campaign card state")
    name = html.escape(str(payload["campaign_name"]))
    text = f"🎰 <b>{name}</b>\n\n{status}"
    winners = [html.escape(str(item["display_name"])) for item in payload.get("winners") or []]
    if winners:
        rows = [f"• {winner}" for winner in winners[:8]]
        if len(winners) > 8:
            rows.append(f"等共 {len(winners)} 人（完整名单见群内结果公告）")
        text += "\n\n🏆 中奖者：\n" + "\n".join(rows)
    return text
