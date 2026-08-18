from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.invitation_models import accepts_attribution
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError


def invite_link_text(campaign_name: str, link: str, *, active: bool) -> str:
    if active:
        note = "仅在本期报名截止前通过该链接加入，才会计算本期邀请加成。"
    else:
        note = "本期已截止；此链接仍可入群，但不再计算本期或以后任何抽奖加成。"
    return f"🔗 {campaign_name} 专属邀请链接\n\n{link}\n\n{note}"


def invite_link_keyboard(link: str, campaign_name: str) -> InlineKeyboardMarkup:
    share = "https://t.me/share/url?url=" + quote(link, safe="")
    share += "&text=" + quote(f"邀请你参加「{campaign_name}」抽奖", safe="")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="打开群聊", url=link)],
            [InlineKeyboardButton(text="转发给好友", url=share)],
        ]
    )


def member_keyboard(
    campaign_reference: str | None = None,
    bonus_enabled: bool = False,
    *,
    invite_enabled: bool | None = None,
) -> InlineKeyboardMarkup:
    ticket_callback = (
        f"member:tickets:{campaign_reference}" if campaign_reference else "member:tickets"
    )
    invite_callback = (
        f"member:invites:{campaign_reference}:0" if campaign_reference else "member:invites"
    )
    rows = [[InlineKeyboardButton(text="我的抽奖次数", callback_data=ticket_callback)]]
    if bonus_enabled and campaign_reference:
        rows.append(
            [
                InlineKeyboardButton(
                    text="我的加成",
                    callback_data=f"member:bonus:{campaign_reference}",
                )
            ]
        )
    show_invitation_record = bonus_enabled if invite_enabled is None else invite_enabled
    if show_invitation_record:
        rows.append([InlineKeyboardButton(text="邀请记录", callback_data=invite_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def legacy_invite_link_payload(
    campaign: object, *, user_id: int, queries: object, cipher: object
) -> tuple[str, InlineKeyboardMarkup]:
    stored = await queries.invite_link_for_participant(campaign.id, user_id)
    if stored is None or stored.encrypted_url is None:
        raise UserFacingError("本期专属链接正在创建，或你尚未完成报名，请稍后再点")
    link = cipher.decrypt(stored.encrypted_url, context=f"invite-link:{stored.id}")
    active = accepts_attribution(
        campaign.state,
        preheat_at=campaign.preheat_at,
        cutoff_at=campaign.cutoff_at,
        event_at=utcnow(),
    )
    return invite_link_text(campaign.name, link, active=active), invite_link_keyboard(
        link, campaign.name
    )
