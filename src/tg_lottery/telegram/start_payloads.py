from tg_lottery.telegram import member_callbacks
from tg_lottery.telegram.member_activity_landing import open_activity
from tg_lottery.telegram.member_share import show_share_card


async def dispatch_start_payload(flow, message, payload: str) -> bool:
    if payload.startswith("join_"):
        await open_activity(flow, message, payload.removeprefix("join_"))
        return True
    if payload.startswith("rules_"):
        await flow.show_rules(message, payload.removeprefix("rules_"))
        return True
    if payload.startswith("share_"):
        await show_share_card(flow, message, payload.removeprefix("share_"))
        return True
    if payload.startswith("invite_"):
        await flow.show_invite_link(
            message, payload.removeprefix("invite_"), user_id=message.from_user.id
        )
        return True
    if payload.startswith("bonus_"):
        await member_callbacks.show_bonus_message(
            flow, message, payload.removeprefix("bonus_"), user_id=message.from_user.id
        )
        return True
    if payload.startswith("ref_"):
        await flow.show_referral_landing(message, payload.removeprefix("ref_"))
        return True
    if not payload.startswith("condition_"):
        return False
    reference, separator, position = payload.removeprefix("condition_").rpartition("_")
    if not separator or not position.isdecimal():
        raise ValueError("invalid participation condition payload")
    await flow.show_condition(message, reference, int(position))
    return True
