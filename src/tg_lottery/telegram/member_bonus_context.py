from dataclasses import replace

from aiogram.types import InlineKeyboardMarkup

from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.member_group_invites import _configured_groups
from tg_lottery.telegram.ui_style import style_keyboard


def actual_bonus_context(context, *, campaign, rules):
    parts = context.route.split(":")
    if parts[1] in {"id", "ad"}:
        return context
    categories = _category_groups(campaign, rules)
    if parts[1] == "bo":
        enabled = tuple(key for key, groups in categories.items() if groups)
        if len(enabled) != 1:
            return context
        category, page = enabled[0], 0
    else:
        category = "invites" if parts[1] == "bonus_invites" else "activity"
        page = int(parts[3])
    visible = categories[category]
    ref = campaign.public_ref
    if len(visible) == 1:
        kind = "id" if category == "invites" else "ad"
        route = f"m:{kind}:{ref}:{visible[0]['id']}:0"
    else:
        route = f"member:bonus_{category}:{ref}:{page}"
    return replace(context, route=route)


def _category_groups(campaign, rules):
    groups = tuple(_configured_groups(campaign, rules))
    return {
        "invites": tuple(
            group for group in groups if rules.invite_target_enabled(int(group["id"]))
        ),
        "activity": tuple(
            group for group in groups if rules.activity_rule_for(int(group["id"])) is not None
        ),
    }


def bonus_eligibility_payload(detail, *, text, keyboard):
    if detail.qualification is QualificationState.ELIGIBLE:
        return text, keyboard
    text += "\n当前参与资格未确认合格；历史奖励保留，新增个人邀请入口需先重新核验条件。"
    rows = [
        [
            button
            for button in row
            if not (button.callback_data or "").startswith(
                ("member:invite_group:", "member:invite:")
            )
        ]
        for row in keyboard.inline_keyboard
    ]
    return text, style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))
