from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.infrastructure.db import InviteLink, TelegramOutbox
from tg_lottery.telegram.crypto import SecretCipher


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
        outbox: TelegramOutbox,
        invite_link: InviteLink | None = None,
    ) -> GatewayResult:
        handlers = {
            "send_campaign_card": self._send_campaign_card,
            "create_invite_link": self._create_invite_link,
            "send_invite_link": self._send_invite_link,
            "revoke_invite_link": self._revoke_invite_link,
            "send_draw_result": self._send_draw_result,
            "send_winner_notice": self._send_winner_notice,
        }
        handler = handlers.get(outbox.operation)
        if handler is None:
            raise ValueError(f"unsupported Telegram outbox operation: {outbox.operation}")
        return await handler(outbox, invite_link)

    async def _send_campaign_card(
        self, outbox: TelegramOutbox, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        deep_link = f"https://t.me/{payload['bot_username']}?start=join_{payload['campaign_id']}"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="立即报名", url=deep_link)],
                [InlineKeyboardButton(text="查看活动规则", url=deep_link)],
            ]
        )
        text = (
            f"🎁 {payload['name']}\n\n"
            f"奖品池（共 {payload['total_prizes']} 份）:\n{_prize_lines(payload)}\n\n"
            f"截止时间: {payload['cutoff_at']}\n"
            f"开奖时间: {payload['draw_at']}\n\n"
            "报名后可通过每日活跃和有效邀请获得抽奖票。"
        )
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            text,
            reply_markup=keyboard,
        )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _create_invite_link(
        self, outbox: TelegramOutbox, _: InviteLink | None
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
        return GatewayResult(
            remote_result=remote_result,
            invite_link=link.invite_link,
        )

    async def _send_invite_link(
        self, outbox: TelegramOutbox, invite_link: InviteLink | None
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
        self, outbox: TelegramOutbox, invite_link: InviteLink | None
    ) -> GatewayResult:
        if invite_link is None or invite_link.encrypted_url is None:
            raise ValueError("revoke_invite_link requires a stored encrypted invite link")
        link = self._cipher.decrypt(
            invite_link.encrypted_url,
            context=f"invite-link:{invite_link.id}",
        )
        result = await self._bot.revoke_chat_invite_link(outbox.recipient_chat_id, link)
        return GatewayResult(remote_result=result.model_dump(mode="json", exclude_none=True))

    async def _send_draw_result(
        self, outbox: TelegramOutbox, _: InviteLink | None
    ) -> GatewayResult:
        winners = outbox.payload.get("winners", [])
        lines = [f"🏆 {outbox.payload['campaign_name']} 开奖结果"]
        if winners:
            for item in winners:
                lines.append(f"• {item['prize_name']}: {item['display_name']}")
        else:
            lines.append("本期没有符合条件的中奖候选人。")
        lines.append(f"快照: {outbox.payload['snapshot_hash']}")
        message = await self._bot.send_message(outbox.recipient_chat_id, "\n".join(lines))
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )

    async def _send_winner_notice(
        self, outbox: TelegramOutbox, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            f"恭喜你在「{payload['campaign_name']}」中获得: {payload['prize_name']}。"
            "请按群内活动说明联系管理员领奖。",
        )
        return GatewayResult(
            remote_result=message.model_dump(mode="json", exclude_none=True),
            remote_message_id=message.message_id,
        )


def invite_link_id(outbox: TelegramOutbox) -> UUID | None:
    value = outbox.payload.get("invite_link_id")
    return UUID(str(value)) if value else None


def _prize_lines(payload: dict[str, Any]) -> str:
    prizes = payload.get("prizes", [])
    return "\n".join(f"• {item['name']} × {item['quantity']}" for item in prizes)
