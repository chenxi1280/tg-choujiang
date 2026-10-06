from aiogram.types import InlineKeyboardButton

from tg_lottery.application.records import utcnow
from tg_lottery.telegram.public_projection import format_public_time


async def profile_conditions(profiles, query, *, rules, reference):
    lines, rows = [], []
    profile = await profiles.get(query.from_user.id)
    if rules.username_required and not query.from_user.username:
        lines.append("用户名缺失：请在Telegram个人设置中设置用户名，回来后重新核验。")
    if rules.allowed_regions:
        lines.append("地区要求：" + "、".join(rules.allowed_regions))
        lines.append("本人地区：" + (profile.region or "未设置"))
        rows.append(
            [InlineKeyboardButton(text="设置本人地区", callback_data=f"m:region:{reference}")]
        )
        if profile.region_locked_until is not None and profile.region_locked_until > utcnow():
            lines.append(
                "地区锁定至：" + format_public_time(profile.region_locked_until, "Asia/Shanghai")
            )
    return lines, rows
