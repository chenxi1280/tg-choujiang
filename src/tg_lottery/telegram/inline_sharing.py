from aiogram.types import (
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InlineQueryResultsButton,
    InputTextMessageContent,
    LinkPreviewOptions,
)

from tg_lottery.domain.public_refs import is_campaign_public_ref
from tg_lottery.telegram.campaign_share_keyboard import campaign_share_keyboard


async def answer_inline_share(bot, query, *, queries, bot_username):
    text = query.query.strip()
    reference = text.removeprefix("share_")
    payload = None
    if text.startswith("share_") and is_campaign_public_ref(reference):
        payload = await queries.campaign_share_payload(reference, bot_username=bot_username)
    results = [inline_share_result(payload)] if payload else []
    await bot.answer_inline_query(
        query.id,
        results=results,
        cache_time=0,
        is_personal=True,
        button=None
        if payload
        else InlineQueryResultsButton(text="从活动详情获取可分享卡片", start_parameter="menu"),
    )


def inline_share_result(payload):
    options = dict(
        id="share:" + payload["campaign_ref"],
        title=payload["name"],
        reply_markup=campaign_share_keyboard(payload),
    )
    text = payload["rendered_card_text"]
    cover = payload.get("presentation", {}).get("cover_file_id")
    if cover:
        return InlineQueryResultCachedPhoto(
            photo_file_id=cover, caption=text, parse_mode="HTML", **options
        )
    return InlineQueryResultArticle(
        input_message_content=InputTextMessageContent(
            message_text=text,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        ),
        **options,
    )
