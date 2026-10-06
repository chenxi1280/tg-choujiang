from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.panel_pagination import titled_pages


async def render_management_panel(
    tokens, *, message, user_id, campaign_id, text, buttons, origin, content_page
):
    pages = titled_pages(text, title=text.split("\n", 1)[0])
    selected = min(max(0, content_page), len(pages) - 1)
    indices = tuple(i for i in (selected - 1, selected + 1) if 0 <= i < len(pages))
    requests = tuple(
        CallbackRequest(
            "view_campaign", campaign_id=campaign_id, payload={"origin": origin, "content_page": i}
        )
        for i in indices
    )
    issued = await tokens.create_many(
        owner_user_id=user_id, chat_id=message.chat.id, requests=requests
    )
    navigation = [
        InlineKeyboardButton(
            text="上一段" if i < selected else "下一段", callback_data="secure:" + token
        )
        for i, token in zip(indices, issued, strict=True)
    ]
    rows = [*buttons, *([navigation] if navigation else [])]
    adjusted = "页码已随当前记录调整。\n" if selected != content_page else ""
    await message.edit_text(
        adjusted + pages[selected], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
