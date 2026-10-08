import html
from typing import Any

from aiogram.types import InlineKeyboardButton

from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.presentation.participation_rules import complete_rule_pages, public_rule_blocks
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.public_projection import campaign_state_text
from tg_lottery.telegram.ui_style import row_keyboard


async def open_activity(flow: Any, message: Any, reference: str) -> None:
    campaign = await flow._queries.campaign_by_reference(reference)
    if campaign is None or campaign.rules.get("participation_rules_version") != "v3.0":
        await flow.register(message, reference)
        return
    await show_v3_rules(flow, message, campaign)


async def show_v3_rules(flow: Any, message: Any, campaign: Any) -> None:
    prizes = await flow._queries.prizes(campaign.id)
    payload = _rules_payload(flow, campaign, prizes)
    pages = complete_rule_pages(
        payload, ("当前状态: " + campaign_state_text(campaign.state), *public_rule_blocks(payload))
    )
    await _send_rule_pages(message, pages, rows=_v3_rules_rows(flow, campaign))


def _rules_payload(flow, campaign, prizes):
    return {
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


def _v3_rules_rows(flow, campaign):
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
            [
                InlineKeyboardButton(
                    text="获取分享卡片",
                    url=f"https://t.me/{flow._bot_username}?start=share_{campaign.public_ref}",
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
    return rows


async def show_legacy_rules(flow, message, campaign):
    prizes = await flow._queries.prizes(campaign.id)
    rules = CampaignRules.model_validate(campaign.rules)
    payload = _rules_payload(flow, campaign, prizes)
    pages = complete_rule_pages(
        payload,
        (
            "当前状态: " + campaign_state_text(campaign.state),
            *public_rule_blocks(payload),
            html.escape(member_condition_views.draw_rule(rules)),
            html.escape(member_condition_views.deletion_rule(campaign)),
        ),
    )
    await _send_rule_pages(
        message, pages, rows=flow._condition_keyboard(campaign).inline_keyboard, label="规则"
    )


async def _send_rule_pages(message, pages, *, rows, label="条件"):
    for index, page in enumerate(pages, 1):
        markup = row_keyboard(rows) if index == len(pages) else None
        await message.answer(
            f"{label} {index}/{len(pages)}\n{page}", parse_mode="HTML", reply_markup=markup
        )
