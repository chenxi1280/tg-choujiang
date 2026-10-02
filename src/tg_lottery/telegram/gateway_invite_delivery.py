from aiogram.types import InlineKeyboardMarkup

from tg_lottery.infrastructure.db import InviteLink
from tg_lottery.telegram.gateway_requests import GatewayRequest
from tg_lottery.telegram.member_invites import invite_link_keyboard, member_keyboard


def invite_delivery_content(
    request: GatewayRequest, invite_link: InviteLink | None, *, cipher: object
) -> tuple[str, InlineKeyboardMarkup]:
    if invite_link is None:
        raise ValueError("send_invite_link requires a stored invite link")
    note = str(request.payload["invite_delivery_note"])
    if not request.payload["invite_entry_visible"]:
        return (
            f"🔗 {request.payload['campaign_name']}\n\n{note}",
            member_keyboard(str(request.payload["campaign_ref"]), invite_enabled=True),
        )
    link = _delivery_link(request, invite_link, cipher=cipher)
    title = str(request.payload.get("group_title") or "本期活动群")
    return (
        f"🔗 你的「{title}」入群入口已就绪。\n\n{note}\n\n{link}\n\n"
        "此入口仅归因到当前活动和群聊，不计入下次抽奖。",
        invite_link_keyboard(link, title, active=request.payload["invite_bonus_active"]),
    )


def _delivery_link(request: GatewayRequest, invite_link: InviteLink, *, cipher: object) -> str:
    referral = str(request.payload.get("referral_url") or "")
    if referral:
        return referral
    if invite_link.encrypted_url is None:
        raise ValueError("send_invite_link requires an encrypted URL")
    return cipher.decrypt(invite_link.encrypted_url, context=f"invite-link:{invite_link.id}")
