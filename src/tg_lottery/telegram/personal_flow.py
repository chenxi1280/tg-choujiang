from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.domain.enums import CampaignState
from tg_lottery.telegram.command_input import parse_bot_command
from tg_lottery.telegram.creator_list_views import creator_list_view
from tg_lottery.telegram.help_views import HelpLinks, guide_summary, guide_topic
from tg_lottery.telegram.home_views import home_keyboard, home_text, settings_keyboard
from tg_lottery.telegram.member_records import show_ticket_record
from tg_lottery.telegram.participant_list_views import (
    build_participated_campaign_list,
    parse_campaign_list_callback,
    parse_invitation_list_callback,
)
from tg_lottery.telegram.public_projection import (
    campaign_state_text,
)

REGIONS = (
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
    "台湾",
)
_REGION_COLUMNS = 3
_DEFAULT_HELP_LINKS = HelpLinks()


class PersonalFlow:
    def __init__(
        self,
        *,
        profiles: UserProfileService,
        queries: QueryService,
        bot_username: str,
        default_timezone: str = "Asia/Shanghai",
        help_links: HelpLinks = _DEFAULT_HELP_LINKS,
    ) -> None:
        self._profiles = profiles
        self._queries = queries
        self._bot_username = bot_username
        self._default_timezone = default_timezone
        self._help_links = help_links

    async def show_guide(self, message: Message, *, edit: bool = False) -> None:
        text, keyboard = guide_summary(self._help_links)
        await _respond(message, text, keyboard=keyboard, edit=edit)

    async def handle_command(self, message: Message, text: str) -> bool:
        command = parse_bot_command(text)
        if command is None or not command.addressed_to(self._bot_username):
            return False
        handlers = {
            "/help": self.show_guide,
            "/index": self.show_created,
            "/join": self.show_joined,
            "/region": self.show_regions,
            "/myid": self.show_user_id,
        }
        handler = handlers.get(command.name)
        if handler is None:
            return False
        await handler(message)
        return True

    async def handle_callback(self, query: CallbackQuery, data: str) -> bool:
        message = _personal_callback_message(query, data)
        if message is None:
            return False
        if data.startswith("nav:guide:"):
            await self._show_guide_topic(message, data)
            return True
        if data.startswith(("nav:created:", "nav:managed:")):
            await self._show_creator_callback(message, data)
            return True
        if data.startswith(("nav:join_at:", "nav:invites_at:")):
            await self._show_anchored_list(message, data)
            return True
        if data.startswith("nav:joined:"):
            await show_ticket_record(
                message,
                self._queries,
                query.from_user.id,
                public_ref=data.removeprefix("nav:joined:"),
            )
            return True
        if data.startswith("nav:join:"):
            category, page = parse_campaign_list_callback(data.removeprefix("nav:join:"))
            await self.show_joined(message, category=category, page=page, edit=True)
            return True
        if data.startswith("nav:invites:"):
            await self._show_invitation_list_callback(message, data)
            return True
        if data.startswith("profile:region:"):
            await self._set_region(query, data.removeprefix("profile:region:"))
            return True
        handlers = {
            "nav:help": self.show_guide,
            "nav:index": self.show_index,
            "nav:join": self.show_joined,
            "nav:invites": self.show_invitation_campaigns,
            "nav:region": self.show_regions,
            "nav:myid": self.show_user_id,
            "nav:created": self.show_created,
            "nav:managed": self.show_managed,
            "nav:settings": self.show_settings,
        }
        handler = handlers.get(data)
        if handler is None:
            return False
        await handler(message, edit=True)
        return True

    async def _show_creator_callback(self, message, data):
        await self._show_created(
            message, data.split(":", 2)[2], managed=data.startswith("nav:managed:"), edit=True
        )

    async def _show_invitation_list_callback(self, message, data):
        category, page, parent = parse_invitation_list_callback(data.removeprefix("nav:invites:"))
        await self.show_invitation_campaigns(
            message, category=category, page=page, parent=parent, edit=True
        )

    async def _show_anchored_list(self, message, data):
        parts = data.split(":")
        anchor = parts[2]
        value = ":".join(parts[3:])
        if parts[1] == "join_at":
            category, page = parse_campaign_list_callback(value)
            await self.show_joined(message, category=category, page=page, anchor=anchor, edit=True)
            return
        category, page, parent = parse_invitation_list_callback(value)
        await self.show_invitation_campaigns(
            message, category=category, page=page, parent=parent, anchor=anchor, edit=True
        )

    async def _show_guide_topic(self, message, data):
        text, keyboard = guide_topic(
            data.removeprefix("nav:guide:"), bot_username=self._bot_username
        )
        await _respond(message, text, keyboard=keyboard, edit=True)

    async def show_index(self, message: Message, *, edit: bool = False) -> None:
        data = await self._queries.creator_navigation_state(_user(message).id)
        await _respond(message, home_text(data), keyboard=home_keyboard(data), edit=edit)

    async def show_settings(self, message: Message, *, edit: bool = False) -> None:
        await _respond(
            message, "个人设置\n\n资料设置不自动登记活动。", keyboard=settings_keyboard(), edit=edit
        )

    async def show_created(self, message: Message, *, edit: bool = False) -> None:
        await self._show_created(message, "active:0", edit=edit)

    async def show_managed(self, message: Message, *, edit: bool = False) -> None:
        await self._show_created(message, "active:0", managed=True, edit=edit)

    async def show_joined(
        self,
        message: Message,
        *,
        category: str = "active",
        page: int = 0,
        anchor: str | None = None,
        edit: bool = False,
    ) -> None:
        result = await self._queries.participated_campaign_page(
            _user(message).id,
            category=category,
            page=page,
            **({"anchor": anchor} if anchor is not None else {}),
        )
        text, keyboard = build_participated_campaign_list(result, invitation_only=False)
        if result.page != page:
            text += "\n列表已变化，已定位到当前活动所在页或有效页。"
        if result.anchor_missing:
            text += "\n原活动已不在当前分类或本人可见范围，请查看当前列表。"
        await _respond(message, text, keyboard=keyboard, edit=edit)

    async def show_invitation_campaigns(
        self,
        message: Message,
        *,
        category: str = "active",
        page: int = 0,
        parent: tuple[str, int] | None = None,
        anchor: str | None = None,
        edit: bool = False,
    ) -> None:
        result = await self._queries.participated_campaign_page(
            _user(message).id,
            category=category,
            page=page,
            invitation_only=True,
            **({"anchor": anchor} if anchor is not None else {}),
        )
        text, keyboard = build_participated_campaign_list(
            result, invitation_only=True, parent=parent
        )
        if result.page != page:
            text += "\n列表已变化，已定位到当前活动所在页或有效页。"
        if result.anchor_missing:
            text += "\n原活动已不在当前分类或本人邀请范围，请查看当前列表。"
        await _respond(message, text, keyboard=keyboard, edit=edit)

    async def show_regions(self, message: Message, *, edit: bool = False) -> None:
        profile = await self._profiles.get(_user(message).id)
        current = profile.region or "未设置"
        text = (
            f"🌏 设置归属地区\n\n当前地区：{current}\n首次设置后 5 分钟内可修改，随后锁定 30 天。"
        )
        keyboard = _region_keyboard()
        if (
            profile.region_locked_until is not None
            and profile.region_editable_until is not None
            and profile.region_editable_until < utcnow() < profile.region_locked_until
        ):
            text += "\n当前锁定至：" + profile.region_locked_until.astimezone(
                ZoneInfo(self._default_timezone)
            ).strftime("%Y年%m月%d日 %H:%M")
            keyboard = settings_keyboard()
        await _respond(
            message,
            text,
            keyboard=keyboard,
            edit=edit,
        )

    async def show_user_id(self, message: Message, *, edit: bool = False) -> None:
        await _respond(
            message,
            f"你的 Telegram 用户 ID：{_user(message).id}",
            keyboard=settings_keyboard(),
            edit=edit,
        )

    async def _show_created(
        self, message: Message, payload: str, *, managed: bool = False, edit: bool = False
    ) -> None:
        parts = payload.split(":")
        if len(parts) not in {1, 2, 3}:
            raise ValueError("创建列表按钮参数无效")
        options = {
            "category": parts[0],
            "page": int(parts[1]) if len(parts) > 1 else 0,
            "managed": managed,
        }
        if len(parts) == 3:
            options["anchor"] = parts[2]
        result = await self._queries.creator_campaign_page(_user(message).id, options=options)
        text, keyboard = creator_list_view(result)
        if result.page != options["page"]:
            text += "\n列表已变化，已定位到当前活动所在页或有效页。"
        if result.anchor_missing:
            text += "\n原活动已不在当前分类或可管理范围，请查看当前列表。"
        await _respond(message, text, keyboard=keyboard, edit=edit)

    async def _set_region(self, query: CallbackQuery, region: str) -> None:
        if region not in REGIONS:
            raise ValueError("地区选项无效")
        message = query.message
        if not isinstance(message, Message) or message.chat.id != query.from_user.id:
            raise ValueError("地区只能在本人私聊中设置")
        profile = await self._profiles.set_region(query.from_user.id, region, utcnow())
        if profile.region_editable_until is None or profile.region_locked_until is None:
            raise RuntimeError("region lock deadlines are missing")
        await message.edit_text(
            _region_success_text(profile, self._default_timezone), reply_markup=_region_keyboard()
        )


def _created_campaign_label(state: CampaignState, category: str) -> str:
    if category == "active":
        return campaign_state_text(state)
    title = {"drawn": "已开奖", "cancelled": "已取消"}[category]
    return f"{title} · 已归档" if state is CampaignState.ARCHIVED else title


def _index_keyboard(
    bot_username: str, links: HelpLinks = _DEFAULT_HELP_LINKS
) -> InlineKeyboardMarkup:
    return home_keyboard()


async def _respond(message, text: str, *, keyboard, edit: bool) -> None:
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
        return
    await message.answer(text, reply_markup=keyboard)


def _region_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=region, callback_data=f"profile:region:{region}")
        for region in REGIONS
    ]
    rows = [
        buttons[index : index + _REGION_COLUMNS]
        for index in range(0, len(buttons), _REGION_COLUMNS)
    ]
    rows.append([InlineKeyboardButton(text="返回个人设置", callback_data="nav:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _region_success_text(profile: object, timezone: str) -> str:
    zone = ZoneInfo(timezone)
    editable = profile.region_editable_until.astimezone(zone).strftime("%Y年%m月%d日 %H:%M")
    locked = profile.region_locked_until.astimezone(zone).strftime("%Y年%m月%d日 %H:%M")
    return f"✅ 已设定为【{profile.region}】\n你仍可在 {editable} 前修改，之后将锁定至 {locked}。"


def _user(message: Message):
    if message.from_user is None:
        raise ValueError("Telegram message has no sender")
    return message.from_user


def _personal_callback_message(query: CallbackQuery, data: str) -> Message | None:
    message = query.message
    if not isinstance(message, Message) or not data.startswith(("nav:", "profile:")):
        return None
    if message.chat.type != "private" or message.chat.id != query.from_user.id:
        return None
    return message.model_copy(update={"from_user": query.from_user})
