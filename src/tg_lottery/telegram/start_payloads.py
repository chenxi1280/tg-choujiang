from tg_lottery.telegram import member_callbacks
from tg_lottery.telegram.member_activity_landing import open_activity
from tg_lottery.telegram.member_share import show_share_card


async def dispatch_start_payload(flow, message, payload: str) -> bool:
    if payload.startswith("history_"):
        from tg_lottery.telegram.public_draw_result import show_public_result

        await show_public_result(flow, message, payload.removeprefix("history_"))
        return True
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
    await _condition_payload(flow, message, payload)
    return True


async def _condition_payload(flow, message, payload):
    reference, separator, position = payload.removeprefix("condition_").rpartition("_")
    if not separator or not position.isdecimal():
        raise ValueError("invalid participation condition payload")
    await flow.show_condition(message, reference, int(position))


async def unpublished_start(message, payload: str) -> bool:
    if payload in {"join_unpublished", "rules_unpublished"} or payload.startswith(
        "condition_unpublished_"
    ):
        await message.answer("这是未发布的抽奖预览，请返回草稿确认发布后再参与。")
        return True
    return False


async def dispatch_handler_start(handler, message, payload: str) -> bool:
    if payload == "create":
        await handler._admin_flow.start_create_message(message)
        return True
    if await unpublished_start(message, payload):
        return True
    if payload.startswith(("join_", "rules_")):
        reference = payload.split("_", 1)[1]
        campaign = await handler._queries.campaign_by_reference(reference)
        if campaign is not None and campaign.rules.get("participation_rules_version") == "v3.0":
            await handler._navigation.open_message(
                message, reference, rules_only=payload.startswith("rules_")
            )
            return True
    return await dispatch_start_payload(handler._member_flow, message, payload)
