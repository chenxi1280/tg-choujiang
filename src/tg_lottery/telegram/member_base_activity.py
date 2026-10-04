from aiogram.types import CallbackQuery, Message

from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram import member_callbacks
from tg_lottery.telegram.member_invites import member_keyboard


async def base_registration_reply(
    message: Message,
    campaign: object,
    *,
    user_id: int,
    callback: CallbackQuery | None,
    queries: object,
) -> None:
    detail = await queries.participant_campaign_detail(user_id, campaign.public_ref)
    if detail is None:
        raise ValueError("registration progress is missing")
    label = "行为门槛" if campaign.rules.get("admission") else "基础发言门槛"
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    if callback is not None and chat_type != "private":
        text = (
            f"已登记参与，{label}已达标，当前共 {detail.total} 次抽奖机会"
            if detail.base_qualified
            else f"已登记，{label}未完成，请查看本场任务进度"
        )
        await member_callbacks.safe_answer(callback, text, alert=False)
        return
    rules = CampaignRules.model_validate(campaign.rules)
    await message.answer(
        "已登记参与本期活动。\n" + detail.base_progress,
        reply_markup=member_keyboard(
            campaign.public_ref,
            bool(rules.bonus_types),
            invite_enabled=rules.invitation_enabled,
        ),
    )
    if callback is not None:
        await member_callbacks.safe_answer(callback, "已更新你的行为门槛进度", alert=False)


async def reply_existing(
    message: Message,
    campaign: object,
    *,
    participant: object,
    callback: CallbackQuery | None,
    queries: object,
    user_id: int | None = None,
) -> None:
    if campaign.rules.get("base_activity_rules") or campaign.rules.get("admission"):
        target_user_id = user_id or getattr(participant, "user_id", None)
        if target_user_id is None and callback is not None and callback.from_user:
            target_user_id = callback.from_user.id
        if target_user_id is None and getattr(message, "from_user", None):
            target_user_id = message.from_user.id
        await base_registration_reply(
            message,
            campaign,
            user_id=target_user_id,
            callback=callback,
            queries=queries,
        )
        return
    await member_callbacks.already_reply(callback, message, participant.ticket_total_cache)


async def registration_total(
    participants: object, campaign: object, *, user_id: int, total: int
) -> int:
    if total:
        return total
    replay = await participants.eligible_participant(campaign.id, user_id)
    return replay.ticket_total_cache if replay is not None else 1


async def qualification_retry_reply(message: Message, callback: CallbackQuery | None) -> None:
    text = "当前无法核验群/频道资格，请稍后再试"
    if callback is not None:
        await member_callbacks.safe_answer(callback, text, alert=True)
        return
    await message.answer(text)
