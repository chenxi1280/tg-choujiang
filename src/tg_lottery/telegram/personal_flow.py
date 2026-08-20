from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.telegram.creator_views import main_keyboard
from tg_lottery.telegram.member_records import show_ticket_record
from tg_lottery.telegram.participant_list_views import (
    build_participated_campaign_list,
    parse_campaign_list_callback,
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


class PersonalFlow:
    def __init__(
        self,
        *,
        profiles: UserProfileService,
        queries: QueryService,
        bot_username: str,
        default_timezone: str = "Asia/Shanghai",
    ) -> None:
        self._profiles = profiles
        self._queries = queries
        self._bot_username = bot_username
        self._default_timezone = default_timezone

    async def show_guide(self, message: Message) -> None:
        await message.answer(
            "📖 抽奖机器人使用指南\n\n"
            "/index  抽奖管理主菜单\n"
            "/new    创建一个抽奖活动\n"
            "/join   查看我参加的抽奖\n"
            "/region 设置我的归属地区\n"
            "/myid   查看我自己的用户ID",
            reply_markup=main_keyboard(),
        )

    async def handle_command(self, message: Message, text: str) -> bool:
        if not text.strip():
            return False
        command = text.split(maxsplit=1)[0].split("@", maxsplit=1)[0]
        handlers = {
            "/index": self.show_index,
            "/join": self.show_joined,
            "/region": self.show_regions,
            "/myid": self.show_user_id,
        }
        handler = handlers.get(command)
        if handler is None:
            return False
        await handler(message)
        return True

    async def handle_callback(self, query: CallbackQuery, data: str) -> bool:
        message = query.message
        if not isinstance(message, Message):
            return False
        if data.startswith("nav:created:"):
            await self._show_created(message, data.removeprefix("nav:created:"))
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
            await self.show_joined(message, category=category, page=page)
            return True
        if data.startswith("nav:invites:"):
            category, page = parse_campaign_list_callback(data.removeprefix("nav:invites:"))
            await self.show_invitation_campaigns(message, category=category, page=page)
            return True
        if data.startswith("profile:region:"):
            await self._set_region(query, data.removeprefix("profile:region:"))
            return True
        handlers = {
            "nav:index": self.show_index,
            "nav:join": self.show_joined,
            "nav:invites": self.show_invitation_campaigns,
            "nav:region": self.show_regions,
            "nav:myid": self.show_user_id,
        }
        handler = handlers.get(data)
        if handler is None:
            return False
        await handler(message)
        return True

    async def show_index(self, message: Message) -> None:
        user = _user(message)
        summary = await self._queries.admin_campaign_summary(user.id)
        await message.answer(
            "🎁 抽奖管理\n\n"
            "客户等级：尚未配置等级规则\n"
            f"累计创建：{summary.total}\n"
            f"进行中：{summary.active}\n"
            f"已开奖：{summary.drawn}\n"
            f"已取消：{summary.cancelled}",
            reply_markup=_index_keyboard(self._bot_username),
        )

    async def show_joined(
        self, message: Message, *, category: str = "active", page: int = 0
    ) -> None:
        result = await self._queries.participated_campaign_page(
            _user(message).id,
            category=category,
            page=page,
        )
        text, keyboard = build_participated_campaign_list(result, invitation_only=False)
        await message.answer(text, reply_markup=keyboard)

    async def show_invitation_campaigns(
        self, message: Message, *, category: str = "active", page: int = 0
    ) -> None:
        result = await self._queries.participated_campaign_page(
            _user(message).id,
            category=category,
            page=page,
            invitation_only=True,
        )
        text, keyboard = build_participated_campaign_list(result, invitation_only=True)
        await message.answer(text, reply_markup=keyboard)

    async def show_regions(self, message: Message) -> None:
        profile = await self._profiles.get(_user(message).id)
        current = profile.region or "未设置"
        await message.answer(
            f"🌏 设置归属地区\n\n当前地区：{current}\n首次设置后 5 分钟内可修改，随后锁定 30 天。",
            reply_markup=_region_keyboard(),
        )

    async def show_user_id(self, message: Message) -> None:
        await message.answer(f"你的 Telegram 用户 ID：{_user(message).id}")

    async def _show_created(self, message: Message, category: str) -> None:
        campaigns = await self._queries.created_campaigns(_user(message).id, category)
        title = {"active": "进行中的抽奖", "drawn": "已开奖", "cancelled": "已取消"}.get(category)
        if title is None:
            raise ValueError("抽奖分类无效")
        if not campaigns:
            await message.answer(
                f"{title}：暂无记录", reply_markup=_index_keyboard(self._bot_username)
            )
            return
        buttons = [
            [
                InlineKeyboardButton(
                    text=f"{item.name}｜{campaign_state_text(item.state)}",
                    callback_data=f"nav:campaign:{item.public_ref}",
                )
            ]
            for item in campaigns
        ]
        await message.answer(
            f"🎁 {title}\n\n点击活动查看详情与当前可用操作。",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    async def _set_region(self, query: CallbackQuery, region: str) -> None:
        if region not in REGIONS:
            raise ValueError("地区选项无效")
        message = query.message
        if not isinstance(message, Message) or message.chat.id != query.from_user.id:
            raise ValueError("地区只能在本人私聊中设置")
        profile = await self._profiles.set_region(query.from_user.id, region, utcnow())
        if profile.region_editable_until is None or profile.region_locked_until is None:
            raise RuntimeError("region lock deadlines are missing")
        await message.edit_text(_region_success_text(profile, self._default_timezone))


def _index_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    group_url = f"https://t.me/{bot_username}?startgroup=lottery"
    channel_url = f"https://t.me/{bot_username}?startchannel=lottery"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="我参加的抽奖", callback_data="nav:join"),
                InlineKeyboardButton(text="我的邀请记录", callback_data="nav:invites"),
            ],
            [InlineKeyboardButton(text="发起抽奖活动", callback_data="nav:create")],
            [
                InlineKeyboardButton(text="进行中", callback_data="nav:created:active"),
                InlineKeyboardButton(text="已开奖", callback_data="nav:created:drawn"),
                InlineKeyboardButton(text="已取消", callback_data="nav:created:cancelled"),
            ],
            [
                InlineKeyboardButton(text="添加到群组", url=group_url),
                InlineKeyboardButton(text="添加到频道", url=channel_url),
            ],
        ]
    )


def _region_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=region, callback_data=f"profile:region:{region}")
        for region in REGIONS
    ]
    rows = [
        buttons[index : index + _REGION_COLUMNS]
        for index in range(0, len(buttons), _REGION_COLUMNS)
    ]
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
