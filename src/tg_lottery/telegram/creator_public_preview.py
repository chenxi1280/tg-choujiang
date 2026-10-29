"""Render a draft through the same projection and transport as publication."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from tg_lottery.application.publication import campaign_card_payload
from tg_lottery.application.records import utcnow
from tg_lottery.infrastructure.db import Campaign, Prize
from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.telegram.draft_config import campaign_input
from tg_lottery.telegram.gateway import _campaign_keyboard, deliver_campaign_card


def preview_payload(data: dict, bot_username: str) -> dict:
    draft, prizes = campaign_input(data, now=utcnow())
    # Transient objects only: preview must never persist a campaign or publication intent.
    campaign = Campaign(
        id=UUID(int=0),
        public_ref="unpublished",
        name=draft.name,
        preheat_at=draft.preheat_at,
        cutoff_at=draft.cutoff_at,
        draw_at=draft.draw_at,
        timezone=draft.rules.timezone,
        rules=draft.rules.model_dump(mode="json"),
        presentation=draft.presentation,
    )
    return campaign_card_payload(
        campaign,
        bot_username,
        [
            Prize(position=index, name=prize.name, winner_count=prize.winner_count)
            for index, prize in enumerate(prizes, start=1)
        ],
    )


async def send_public_preview(bot, message, *, data: dict, bot_username: str) -> None:
    payload = preview_payload(data, bot_username)
    text, pages = rule_manifest(payload)
    await message.answer(
        "以下为发布效果预览（未发布）。图片、正文和按钮布局沿用正式公告；"
        "活动链接及群内规则定位在发布后生成，预览不能报名。"
    )
    for index, page in enumerate(pages, start=1):
        await bot.send_message(
            message.chat.id,
            f"完整规则 {index}/{len(pages)}\n{page}",
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=button.text, callback_data="preview:unpublished")
                for button in row
            ]
            for row in _campaign_keyboard(payload).inline_keyboard
        ]
    )
    await deliver_campaign_card(
        bot, message.chat.id, payload | {"rendered_card_text": text}, keyboard=keyboard
    )
