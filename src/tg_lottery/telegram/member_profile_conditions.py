from aiogram.types import InlineKeyboardButton

from tg_lottery.application.records import utcnow
from tg_lottery.telegram.public_projection import format_public_time


async def profile_conditions(profiles, query, *, rules, reference):
    lines, rows = [], []
    profile = await profiles.get(query.from_user.id)
    if rules.username_required:
        lines.append("用户名要求：必须设置Telegram用户名。")
        if query.from_user.username:
            lines.append("本人用户名：@" + query.from_user.username + "（已满足）")
        else:
            lines.append("用户名缺失：请在Telegram个人设置中设置用户名，回来后重新核验。")
    else:
        lines.append("用户名要求：可为空。")
    if rules.allowed_regions:
        lines.append("地区要求：" + "、".join(rules.allowed_regions))
        lines.append("本人地区：" + (profile.region or "未设置"))
        locked = region_locked(profile, utcnow())
        matched = profile.region in rules.allowed_regions
        if locked:
            lines.append(
                "地区锁定至：" + format_public_time(profile.region_locked_until, "Asia/Shanghai")
            )
            if not matched:
                lines.append("本人地区与允许值不符，当前锁定期间无法修改或满足该项条件。")
        else:
            rows.append(
                [InlineKeyboardButton(text="设置本人地区", callback_data=f"m:region:{reference}")]
            )
        if matched:
            lines.append("地区条件已满足。")
    else:
        lines.append("地区要求：不限。")
    return lines, rows


def region_locked(profile, now) -> bool:
    return (
        profile.region_editable_until is not None
        and now > profile.region_editable_until
        and profile.region_locked_until is not None
        and now < profile.region_locked_until
    )
