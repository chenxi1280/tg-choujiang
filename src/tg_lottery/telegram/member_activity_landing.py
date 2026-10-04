from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.participation_rules import public_rule_blocks, split_rule_blocks
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.public_projection import campaign_state_text, format_public_time


async def open_activity(flow: Any, message: Any, reference: str) -> None:
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None or campaign.rules.get("participation_rules_version") != "v3.0":
        await flow.register(message, reference)
        return
    await show_v3_rules(flow, message, campaign)


async def show_v3_rules(flow: Any, message: Any, campaign: Any) -> None:
    prizes = await flow._queries.prizes(campaign.id)
    payload = {
        "name": campaign.name,
        "campaign_ref": campaign.public_ref,
        "bot_username": flow._bot_username,
        "rules": campaign.rules,
        "presentation": campaign.presentation,
        "cutoff_at": campaign.cutoff_at.isoformat(),
        "draw_at": campaign.draw_at.isoformat(),
        "timezone": campaign.timezone,
        "prizes": [{"name": item.name, "winner_count": item.winner_count} for item in prizes],
    }
    pages = split_rule_blocks(
        ("当前状态: " + campaign_state_text(campaign.state), *public_rule_blocks(payload))
    )
    open_state = campaign.state in {CampaignState.RUNNING, CampaignState.SCHEDULED}
    from tg_lottery.application.records import utcnow

    open_state = open_state and utcnow() <= campaign.cutoff_at
    rows = flow._condition_keyboard(campaign).inline_keyboard
    if open_state:
        rows = [
            [
                InlineKeyboardButton(
                    text="我已阅读，登记/查看本人进度",
                    callback_data=f"member:join:{campaign.public_ref}",
                )
            ],
            *rows,
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text="查看本人历史记录", callback_data=f"member:tickets:{campaign.public_ref}"
                )
            ]
        ]
    for index, page in enumerate(pages, 1):
        markup = InlineKeyboardMarkup(inline_keyboard=rows) if index == len(pages) else None
        await message.answer(
            f"条件 {index}/{len(pages)}\n{page}", parse_mode="HTML", reply_markup=markup
        )


async def show_legacy_rules(flow, message, campaign):
    prizes = await flow._queries.prizes(campaign.id)
    rules = CampaignRules.model_validate(campaign.rules)
    lines = [
        f"📋 {campaign.name} 活动规则",
        f"当前状态: {campaign_state_text(campaign.state)}",
        "奖品: " + "、".join(f"{item.name} × {item.winner_count}" for item in prizes),
        f"开奖时间: {format_public_time(campaign.draw_at, campaign.timezone)}",
        member_condition_views.draw_rule(rules),
        member_condition_views.human_rule(rules, member_condition_views.conditions(campaign)),
        f"发奖人员: {member_condition_views.issuer_name(campaign)}",
        member_condition_views.deletion_rule(campaign),
        member_condition_views.condition_summary(campaign),
    ]
    await message.answer("\n".join(lines), reply_markup=flow._condition_keyboard(campaign))
