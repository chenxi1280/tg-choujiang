from aiogram.types import InlineKeyboardButton

PAGE_SIZE = 6


def group_detail_row(
    reference: str, category: str, group: dict[str, object], *, group_page: int
) -> list[InlineKeyboardButton]:
    callback = "id" if category == "invites" else "ad"
    data = f"m:{callback}:{reference}:{group['id']}:{group_page}"
    return [InlineKeyboardButton(text=str(group["title"]), callback_data=data)]


def visible_page(
    groups: tuple[dict[str, object], ...], page: int
) -> tuple[tuple[dict[str, object], ...], int, int]:
    total_pages = max(1, (len(groups) + PAGE_SIZE - 1) // PAGE_SIZE)
    selected = min(max(0, page), total_pages - 1)
    start = selected * PAGE_SIZE
    return groups[start : start + PAGE_SIZE], selected, total_pages


def navigation_rows(
    *, reference: str, category: str, page: int, total_pages: int
) -> list[list[InlineKeyboardButton]]:
    prefix = "bonus_invites" if category == "invites" else "bonus_activity"
    buttons = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(
                text="上一页", callback_data=f"member:{prefix}:{reference}:{page - 1}"
            )
        )
    if page + 1 < total_pages:
        buttons.append(
            InlineKeyboardButton(
                text="下一页", callback_data=f"member:{prefix}:{reference}:{page + 1}"
            )
        )
    return [buttons] if buttons else []


def page_row(reference: str, page: int, maximum: int) -> list[InlineKeyboardButton]:
    previous = max(0, page - 1)
    following = min(maximum, page + 1)
    return [
        InlineKeyboardButton(
            text="上一页", callback_data=f"member:invite_page:{reference}:{previous}"
        ),
        InlineKeyboardButton(text=f"{page + 1}/{maximum + 1}", callback_data="member:noop"),
        InlineKeyboardButton(
            text="下一页", callback_data=f"member:invite_page:{reference}:{following}"
        ),
    ]


def footer_rows(reference: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text="🔄 刷新加成状态", callback_data=f"member:bonus:{reference}")],
        [InlineKeyboardButton(text="↩ 返回活动规则", callback_data=f"member:rules:{reference}")],
    ]
