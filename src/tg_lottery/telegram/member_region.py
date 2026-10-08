from aiogram.types import InlineKeyboardButton

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.application.records import utcnow
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_profile_conditions import region_locked
from tg_lottery.telegram.personal_flow import REGIONS
from tg_lottery.telegram.public_projection import format_public_time
from tg_lottery.telegram.ui_style import row_keyboard

REGION_COLUMNS = 2


async def region_view(profiles, tokens, *, user_id: int, context):
    profile = await profiles.get(user_id)
    now = utcnow()
    locked = region_locked(profile, now)
    text = "本人地区\n当前地区：" + (profile.region or "未设置")
    rows = []
    if locked:
        text += "\n锁定至：" + format_public_time(profile.region_locked_until, "Asia/Shanghai")
    else:
        text += "\n首次设置后5分钟内可修改，随后锁定30天。设置资料不会自动登记活动。"
        requests = tuple(
            CallbackRequest(
                "member:region_set",
                payload={"region": region, "route": context.route, "trail": list(context.trail)},
            )
            for region in REGIONS
        )
        issued = await tokens.create_many(owner_user_id=user_id, chat_id=user_id, requests=requests)
        buttons = [
            InlineKeyboardButton(text=region, callback_data="secure:" + token)
            for region, token in zip(REGIONS, issued, strict=True)
        ]
        rows = [buttons[i : i + REGION_COLUMNS] for i in range(0, len(buttons), REGION_COLUMNS)]
    ref = context.route.split(":")[2]
    rows.append(
        [InlineKeyboardButton(text="返回参与条件检查", callback_data=f"m:conditions:{ref}:0")]
    )
    return text, row_keyboard(rows)


async def set_region(profiles, query, grant):
    region = grant.payload["region"]
    if region not in REGIONS:
        raise UserFacingError("地区选项无效")
    if query.message.chat.type != "private" or query.message.chat.id != query.from_user.id:
        raise UserFacingError("地区只能在本人私聊中设置")
    await profiles.set_region(query.from_user.id, region, utcnow())
