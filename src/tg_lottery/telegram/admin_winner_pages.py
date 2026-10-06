from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.application.winner_queries import WINNER_PAGE_SIZE
from tg_lottery.domain.enums import CampaignState, PrizeClaimState
from tg_lottery.telegram.panel_pagination import titled_pages

CLAIM_LABELS = {
    PrizeClaimState.CLAIMED: "已登记领奖",
    PrizeClaimState.PENDING: "待登记领奖",
    PrizeClaimState.FORFEITED: "已放弃领奖",
}


async def winner_page_view(queries, tokens, *, query, grant):
    payload = grant.payload or {}
    page = await queries.winners_page(grant.campaign_id, page=int(payload.get("page", 0)))
    campaign = await queries.campaign(grant.campaign_id)
    requests, labels = _winner_actions(page, grant, state=campaign.state)
    issued = await tokens.create_many(
        owner_user_id=query.from_user.id, chat_id=query.message.chat.id, requests=tuple(requests)
    )
    rows = [
        [InlineKeyboardButton(text=label, callback_data="secure:" + token)]
        for label, token in zip(labels, issued, strict=True)
    ]
    text = f"{campaign.name} · 领奖登记\n共{page.total}个名额 · 第{page.page + 1}/{page.pages}页\n"
    text += "\n\n".join(
        f"{i}. {row.participant.display_name}（用户ID {row.participant.user_id}）"
        f"\n{row.prize.name} · 名额{row.slot} · " + CLAIM_LABELS[row.winner.claim_state]
        for i, row in enumerate(page.rows, page.page * WINNER_PAGE_SIZE + 1)
    )
    pages = titled_pages(text, title=campaign.name + " · 领奖登记")
    selected = min(max(0, int(payload.get("content_page", 0))), len(pages) - 1)
    rows.extend(
        await _content_rows(tokens, query, grant=grant, selected=selected, total=len(pages))
    )
    await query.message.edit_text(
        pages[selected], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )


def _winner_actions(page, grant, *, state):
    requests, labels = [], []
    for number, row in enumerate(page.rows, page.page * WINNER_PAGE_SIZE + 1):
        if row.winner.claim_state is PrizeClaimState.PENDING and state is CampaignState.DRAWN:
            payload = dict(grant.payload or {}) | {
                "winner_id": str(row.winner.id),
                "winner_name": row.participant.display_name,
                "winner_user_id": row.participant.user_id,
                "prize_name": row.prize.name,
                "slot": row.slot,
            }
            requests.append(
                CallbackRequest("mark_claimed", campaign_id=grant.campaign_id, payload=payload)
            )
            labels.append(f"登记第{number}项已领奖")
    for label, number in (("上一页", page.page - 1), ("下一页", page.page + 1)):
        if 0 <= number < page.pages:
            requests.append(
                CallbackRequest(
                    "view_winners",
                    campaign_id=grant.campaign_id,
                    payload=dict(grant.payload or {}) | {"page": number, "content_page": 0},
                )
            )
            labels.append(label)
    requests.append(
        CallbackRequest("view_campaign", campaign_id=grant.campaign_id, payload=grant.payload)
    )
    labels.append("返回活动管理")
    return requests, labels


async def _content_rows(tokens, query, *, grant, selected, total):
    indices = tuple(i for i in (selected - 1, selected + 1) if 0 <= i < total)
    requests = tuple(
        CallbackRequest(
            "view_winners",
            campaign_id=grant.campaign_id,
            payload=dict(grant.payload or {}) | {"content_page": i},
        )
        for i in indices
    )
    issued = await tokens.create_many(
        owner_user_id=query.from_user.id, chat_id=query.message.chat.id, requests=requests
    )
    buttons = [
        InlineKeyboardButton(
            text="上一段" if i < selected else "下一段", callback_data="secure:" + token
        )
        for i, token in zip(indices, issued, strict=True)
    ]
    return [buttons] if buttons else []
