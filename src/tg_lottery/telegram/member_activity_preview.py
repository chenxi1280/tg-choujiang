from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.presentation.participation_rules import rule_manifest
from tg_lottery.telegram.member_activity_landing import _rules_payload
from tg_lottery.telegram.public_projection import campaign_state_text


async def show_activity_preview(flow, query, context):
    reference = context.route.split(":")[2]
    campaign = await flow._campaign(reference)
    prizes = await flow._queries.prizes(campaign.id)
    text, _ = rule_manifest(_rules_payload(flow, campaign, prizes))
    issues = await flow._queries.operational_issues(campaign.id)
    rows = flow._rule_rows(campaign, None, blocked=bool(issues))
    rows.insert(-1, [InlineKeyboardButton(text="完整规则", callback_data=f"m:rules:{reference}:0")])
    outcome = await flow._queries.campaign_outcome(campaign.id)
    if outcome.kind == "drawn":
        rows.insert(
            -1,
            [
                InlineKeyboardButton(
                    text="查看开奖结果",
                    url=f"https://t.me/{flow._bot_username}?start=history_{reference}",
                )
            ],
        )
    await flow._render(
        query,
        context,
        pages=(f"活动详情 · {campaign_state_text(campaign.state)}\n尚未登记本场活动\n\n{text}",),
        keyboard=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML",
    )
