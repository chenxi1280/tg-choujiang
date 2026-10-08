from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.application.callback_tokens import CallbackRequest
from tg_lottery.telegram.keyboard_layout import fit_rows
from tg_lottery.telegram.ui_style import plain_label, style_keyboard

READ_PREFIXES = (
    "m:detail:",
    "m:region:",
    "m:gate:",
    "m:gg:",
    "m:conditions:",
    "m:rules:",
    "m:bo:",
    "m:id:",
    "m:ad:",
    "m:ir:",
    "member:tickets:",
    "member:invites:",
    "member:bonus_invites:",
    "member:bonus_activity:",
    "member:rules:",
    "nav:join",
    "nav:invites",
    "nav:index",
)


@dataclass(frozen=True)
class MemberPageContext:
    route: str
    trail: tuple[str, ...] = ()
    content_page: int = 0


async def scoped_keyboard(tokens, keyboard, *, context: MemberPageContext, user_id: int):
    keyboard = keyboard.model_copy(update={"inline_keyboard": fit_rows(keyboard.inline_keyboard)})
    descriptors = [
        _descriptor(button, context) for row in keyboard.inline_keyboard for button in row
    ]
    requests = tuple(
        CallbackRequest("member:navigate", payload=value)
        for value in descriptors
        if value is not None
    )
    issued = iter(
        await tokens.create_many(owner_user_id=user_id, chat_id=user_id, requests=requests)
    )
    rows = []
    cursor = iter(descriptors)
    for row in keyboard.inline_keyboard:
        buttons = []
        for button in row:
            descriptor = next(cursor)
            if descriptor is None:
                buttons.append(button)
                continue
            label = _return_label(descriptor["route"]) if descriptor["back"] else button.text
            buttons.append(InlineKeyboardButton(text=label, callback_data="secure:" + next(issued)))
        rows.append(buttons)
    return style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))


def _descriptor(button, context: MemberPageContext) -> dict | None:
    route = button.callback_data
    if route and route.startswith("m:page:"):
        return {
            "route": context.route,
            "trail": list(context.trail),
            "back": False,
            "content_page": int(route.removeprefix("m:page:")),
        }
    if route is None or not route.startswith(READ_PREFIXES):
        return None
    back = (
        plain_label(button.text).startswith("返回") and route != "nav:index" and bool(context.trail)
    )
    if back:
        return {"route": context.trail[-1], "trail": list(context.trail[:-1]), "back": True}
    if route in context.trail:
        return {
            "route": route,
            "trail": list(context.trail[: context.trail.index(route)]),
            "back": False,
        }
    if _page_family(route) == _page_family(context.route):
        return {"route": route, "trail": list(context.trail), "back": False}
    return {"route": route, "trail": [*context.trail, context.route], "back": False}


def _page_family(route: str) -> str:
    parts = route.split(":")
    if route.startswith(
        (
            "m:gate:",
            "m:rules:",
            "m:conditions:",
            "member:bonus_invites:",
            "member:bonus_activity:",
            "member:invites:",
        )
    ):
        return ":".join(parts[:3])
    if route.startswith("m:ir:"):
        return ":".join(parts[:4])
    return route


def _return_label(route: str) -> str:
    labels = (
        ("nav:join", "返回参与列表"),
        ("nav:invites", "返回邀请活动列表"),
        ("nav:index", "返回首页"),
        ("m:detail:", "返回活动详情"),
        ("member:tickets:", "返回活动详情"),
        ("m:bo:", "返回加成明细"),
        ("m:gate:", "返回门槛进度"),
        ("member:invites:", "返回邀请群列表"),
    )
    return next((label for prefix, label in labels if route.startswith(prefix)), "返回来源页面")
