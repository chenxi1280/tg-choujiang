from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tg_lottery.application.queries import QueryService
from tg_lottery.application.records import utcnow
from tg_lottery.application.user_profiles import UserProfileService
from tg_lottery.telegram.creator_views import main_keyboard

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
    ) -> None:
        self._profiles = profiles
        self._queries = queries
        self._bot_username = bot_username

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
        if data.startswith("profile:region:"):
            await self._set_region(query, data.removeprefix("profile:region:"))
            return True
        handlers = {
            "nav:index": self.show_index,
            "nav:join": self.show_joined,
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
            f"累计创建：{summary.total}\n"
            f"进行中：{summary.active}\n"
            f"已开奖：{summary.drawn}\n"
            f"已取消：{summary.cancelled}",
            reply_markup=_index_keyboard(self._bot_username),
        )

    async def show_joined(self, message: Message) -> None:
        rows = await self._queries.participated_campaigns(_user(message).id)
        if not rows:
            await message.answer("您未参与任何抽奖")
            return
        lines = [
            f"• {row.name}｜{row.state.value}｜资格 {row.qualification.value}｜"
            f"{row.tickets} 票｜开奖 {row.draw_at:%Y-%m-%d %H:%M}"
            for row in rows
        ]
        await message.answer("🎟 我参加的抽奖\n\n" + "\n".join(lines))

    async def show_regions(self, message: Message) -> None:
        profile = await self._profiles.get(_user(message).id)
        current = profile.region or "未设置"
        await message.answer(
            f"🌏 设置归属地区\n\n当前地区：{current}\n首次设置后 5 分钟内可修改，随后锁定 30 天。",
            reply_markup=_region_keyboard(),
        )

    async def show_user_id(self, message: Message) -> None:
        await message.answer(f"你的用户ID: {_user(message).id}")

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
        lines = [
            f"• {item.name}｜{item.state.value}｜{item.draw_at:%Y-%m-%d %H:%M}"
            for item in campaigns
        ]
        await message.answer(f"🎁 {title}\n\n" + "\n".join(lines))

    async def _set_region(self, query: CallbackQuery, region: str) -> None:
        if region not in REGIONS:
            raise ValueError("地区选项无效")
        message = query.message
        if not isinstance(message, Message) or message.chat.id != query.from_user.id:
            raise ValueError("地区只能在本人私聊中设置")
        profile = await self._profiles.set_region(query.from_user.id, region, utcnow())
        await message.edit_text(f"✅ 归属地区已设置为：{profile.region}")


def _index_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    group_url = f"https://t.me/{bot_username}?startgroup=lottery"
    channel_url = f"https://t.me/{bot_username}?startchannel=lottery"
    return InlineKeyboardMarkup(
        inline_keyboard=[
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


def _user(message: Message):
    if message.from_user is None:
        raise ValueError("Telegram message has no sender")
    return message.from_user
