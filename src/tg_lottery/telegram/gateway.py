import base64
import html
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from tg_lottery.application.message_deletion import TEMPORARY_REMINDER_OPERATION
from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.presentation.campaign_card import (
    campaign_link,
    legacy_participation_footer,
    participation_footer,
)
from tg_lottery.presentation.draw_messages import creator_draw_notice_text, winner_notice_text
from tg_lottery.presentation.draw_messages import draw_result_text as _draw_result_text
from tg_lottery.presentation.mentions import (
    user_mention,
)
from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.presentation.progress_notice import progress_notice_html
from tg_lottery.telegram.crypto import SecretCipher
from tg_lottery.telegram.gateway_card_states import (
    campaign_state_card_text as _campaign_state_card_text,
)
from tg_lottery.telegram.gateway_edit import confirmed_edit
from tg_lottery.telegram.gateway_invite_delivery import invite_delivery_content
from tg_lottery.telegram.gateway_requests import GatewayRequest
from tg_lottery.telegram.remote_serialization import remote_object
from tg_lottery.telegram.ui_style import style_keyboard

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayResult:
    remote_result: dict[str, Any]
    remote_message_id: int | None = None
    invite_link: str | None = None


class TelegramGateway:
    def __init__(self, bot: Bot, cipher: SecretCipher, *, bot_username: str | None = None) -> None:
        self._bot = bot
        self._cipher = cipher
        self._bot_username = bot_username

    async def execute(
        self,
        outbox: GatewayRequest,
        invite_link: InviteLink | None = None,
    ) -> GatewayResult:
        handlers = {
            "send_campaign_card": self._send_campaign_card,
            "send_campaign_share_card": self._send_campaign_share_card,
            "send_campaign_rules": self._campaign_rules,
            "edit_campaign_rules": self._campaign_rules,
            "edit_campaign_card_cancelled": self._edit_campaign_card_cancelled,
            "edit_campaign_card_state": self._edit_campaign_card_state,
            "create_invite_link": self._create_invite_link,
            "create_required_chat_invite": self._create_required_chat_invite,
            "send_invite_link": self._send_invite_link,
            "revoke_invite_link": self._revoke_invite_link,
            "revoke_required_chat_invite": self._revoke_required_chat_invite,
            "send_draw_result": self._send_draw_result,
            "send_winner_notice": self._send_winner_notice,
            "send_creator_draw_notice": self._send_creator_draw_notice,
            "send_progress_notice": self._send_progress_notice,
            TEMPORARY_REMINDER_OPERATION: self._send_temporary_reminder,
            "delete_message": self._delete_message,
        }
        handler = handlers.get(outbox.operation)
        if handler is None:
            raise ValueError(f"unsupported Telegram outbox operation: {outbox.operation}")
        return await handler(outbox, invite_link)

    async def _send_progress_notice(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        mention = user_mention(payload)
        text = (
            f"{mention}\n{html.escape(str(payload['campaign_name']))}\n"
            f"{progress_notice_html(payload, bot_username=self._bot_username)}\n"
            f"{html.escape(str(payload['ticket_status']))}"
        )
        if payload.get("occurred_at"):
            from tg_lottery.telegram.public_projection import format_public_time

            text += "\n完成时间: " + format_public_time(payload["occurred_at"], "Asia/Shanghai")
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            text,
            parse_mode="HTML",
            disable_notification=False,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=style_keyboard(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="查看我的进度",
                                callback_data=f"member:tickets:{payload['campaign_ref']}",
                            )
                        ]
                    ]
                )
            ),
        )
        return GatewayResult(
            remote_result=remote_object(message), remote_message_id=message.message_id
        )

    async def _send_temporary_reminder(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        options = {"disable_notification": False}
        text = str(payload["text"])
        link = None
        if payload.get("campaign_ref"):
            if not self._bot_username:
                raise ValueError("temporary reminder participation link requires a bot username")
            link = campaign_link(payload | {"bot_username": self._bot_username})
            options["reply_markup"] = style_keyboard(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="参与抽奖",
                                url=link,
                            ),
                        ]
                    ]
                )
            )
        if link or payload.get("user_id"):
            text = html.escape(text)
            options["parse_mode"] = "HTML"
        if link:
            text += f'\n查看本场活动：<a href="{html.escape(link, quote=True)}">点击参与</a>'
        if payload.get("user_id"):
            mention = user_mention(payload | {"display_name": "这位成员"})
            text = mention + "\n" + text
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            text,
            **options,
        )
        return GatewayResult(
            remote_result=remote_object(message), remote_message_id=message.message_id
        )

    async def _campaign_rules(self, outbox, invite_link):
        from tg_lottery.telegram.gateway_rule_pages import deliver_rule_page

        return await deliver_rule_page(self._bot, outbox)

    async def _send_campaign_card(self, outbox, invite_link) -> GatewayResult:
        message = await self._deliver_card(outbox)
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
            remote_result=remote_object(message), remote_message_id=message.message_id
        )

    async def _send_campaign_share_card(self, outbox, invite_link) -> GatewayResult:
        if outbox.recipient_chat_id <= 0:
            raise ValueError("share cards must be delivered to the requesting private chat")
        message = await self._deliver_card(outbox)
        return GatewayResult(
            remote_result=remote_object(message), remote_message_id=message.message_id
        )

    async def _deliver_card(self, outbox):
        return await deliver_campaign_card(self._bot, outbox.recipient_chat_id, outbox.payload)

    async def _edit_campaign_card_cancelled(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        name = html.escape(str(payload["campaign_name"]))
        text = f"✨ <b>{name}</b>\n\n活动已取消，报名入口已关闭。"
        if payload.get("frozen_rules_text"):
            text = "活动已取消，报名入口已关闭。\n\n" + payload["frozen_rules_text"]
        return await self._edit_card(outbox, text)

    async def _edit_campaign_card_state(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        return await self._edit_card(outbox, _campaign_state_card_text(outbox.payload))

    async def _edit_card(self, outbox: GatewayRequest, text: str) -> GatewayResult:
        message_id = int(outbox.payload["message_id"])
        options = dict(chat_id=outbox.recipient_chat_id, message_id=message_id, parse_mode="HTML")
        operation = (
            self._bot.edit_message_caption(caption=text, **options)
            if outbox.payload.get("has_cover")
            else self._bot.edit_message_text(text, **options)
        )
        return await confirmed_edit(operation, message_id=message_id)

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
        remote_result = remote_object(link)
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
        remote_result = remote_object(link)
        remote_result.pop("invite_link", None)
        return GatewayResult(remote_result=remote_result, invite_link=link.invite_link)

    async def _send_invite_link(
        self, outbox: GatewayRequest, invite_link: InviteLink | None
    ) -> GatewayResult:
        text, keyboard = invite_delivery_content(outbox, invite_link, cipher=self._cipher)
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            text,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        return GatewayResult(
            remote_result=remote_object(message),
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
        return GatewayResult(remote_result=remote_object(result))

    async def _revoke_required_chat_invite(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        payload = outbox.payload
        encrypted = base64.urlsafe_b64decode(str(payload["join_url_encrypted"]).encode())
        link = self._cipher.decrypt(
            encrypted,
            context=str(
                payload.get("secret_context")
                or f"required-chat:{payload['campaign_id']}:{int(payload['chat_id'])}"
            ),
        )
        result = await self._bot.revoke_chat_invite_link(outbox.recipient_chat_id, link)
        return GatewayResult(remote_result=remote_object(result))

    async def _send_draw_result(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            _draw_result_text(outbox.payload | {"bot_username": self._bot_username}),
            disable_notification=False,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return GatewayResult(
            remote_result=remote_object(message),
            remote_message_id=message.message_id,
        )

    async def _send_winner_notice(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            winner_notice_text(outbox.payload | {"bot_username": self._bot_username}),
            disable_notification=False,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return GatewayResult(
            remote_result=remote_object(message),
            remote_message_id=message.message_id,
        )

    async def _delete_message(self, outbox: GatewayRequest, _: InviteLink | None) -> GatewayResult:
        result = await self._bot.delete_message(
            outbox.recipient_chat_id, int(outbox.payload["message_id"])
        )
        return GatewayResult(remote_result={"ok": bool(result)})

    async def _send_creator_draw_notice(
        self, outbox: GatewayRequest, _: InviteLink | None
    ) -> GatewayResult:
        message = await self._bot.send_message(
            outbox.recipient_chat_id,
            creator_draw_notice_text(outbox.payload | {"bot_username": self._bot_username}),
            disable_notification=False,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return GatewayResult(
            remote_result=remote_object(message), remote_message_id=message.message_id
        )


def _campaign_deep_link(payload: dict[str, Any], action: str) -> str:
    return campaign_link(payload, action)


def _campaign_keyboard(payload: dict[str, Any]) -> InlineKeyboardMarkup:
    from tg_lottery.telegram.campaign_share_keyboard import campaign_share_keyboard

    return campaign_share_keyboard(payload)


def _campaign_card_text(payload: dict[str, Any]) -> str:
    if payload.get("rendered_card_text"):
        return str(payload["rendered_card_text"]).replace(
            legacy_participation_footer(payload), participation_footer(payload)
        )
    if not payload.get("timezone"):
        raise ValueError("campaign card timezone is required")
    text, _pages = rule_manifest(payload)
    return text


async def deliver_campaign_card(bot, chat_id, payload, *, keyboard=None):
    text = _campaign_card_text(payload)
    options = dict(
        reply_markup=keyboard if keyboard is not None else _campaign_keyboard(payload),
        parse_mode="HTML",
        protect_content=False,
    )
    cover = payload.get("presentation", {}).get("cover_file_id")
    if cover:
        return await bot.send_photo(
            chat_id,
            cover,
            caption=text,
            show_caption_above_media=False,
            **options,
        )
    return await bot.send_message(
        chat_id,
        text,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
        **options,
    )
