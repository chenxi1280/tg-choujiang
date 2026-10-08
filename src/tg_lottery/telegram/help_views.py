from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.draft_contract import DRAFT_SESSION_TTL_HOURS
from tg_lottery.telegram.keyboard_layout import columns
from tg_lottery.telegram.ui_style import style_keyboard

DRAFT_UNAVAILABLE_TEXT = (
    "当前没有可继续的草稿。\n"
    f"未发布草稿在最后一次更新后保留 {DRAFT_SESSION_TTL_HOURS} 小时，"
    "请发送 /new 重新创建。"
)


@dataclass(frozen=True)
class HelpLinks:
    guide: str = ""
    navigation: str = ""
    featured: str = ""

    def rows(self) -> list[list[InlineKeyboardButton]]:
        links = (
            ("📖 使用说明频道", self.guide),
            ("📣 抽奖导航频道", self.navigation),
            ("⭐ 精选抽奖频道", self.featured),
        )
        return columns([InlineKeyboardButton(text=title, url=url) for title, url in links if url])


GUIDE_TEXT = f"""📖 抽奖机器人使用指南

➕ 发起抽奖
👥 将机器人加入参与群和条件频道并设为管理员。私有群/频道需开启邀请用户权限。
🏷️ 点击“创建抽奖”，选择参与群，再设置标题、奖品和开奖时间。
🧭 在“准入条件”添加需要关注的频道或加入的群；可添加多个，参与者必须全部满足。
🚪 在“抽奖门槛”二选一设置发言数或邀请好友，各配置群必须分别达标。
📈 “活跃加成（人品）”和“邀请加成”分别设置各群奖励。发布前核对名称、全部条件和时间。
📝 未发布草稿在最后一次更新后保留 {DRAFT_SESSION_TTL_HOURS} 小时；发送 /new 可继续有效草稿。

🛂 参加抽奖
打开活动卡的参与链接，先阅读完整条件，再点击“登记参与”或“查看本人进度”。
按提示关注频道、加入群并验证资格；首次核验通过才开始计算本场发言与邀请。
只查看规则或入群不会自动报名，登记前的行为不补计。历史活动沿用原报名规则。
加入申请需等待管理员批准。发言门槛、用户名或地区要求以本次活动规则为准。
“我参加的抽奖”可查看报名状态、抽奖次数和结果。

📈 邀请与活跃加成
报名后进入“我的加成”，选择邀请加成和目标群，复制本期邀请链接分享。
每次抽奖、每个群分别累计；有效邀请完成留存与核验后自动计入，无需手动领取。
点击链接、已经在群内或未完成核验均不代表获得加成。
本期新生成的邀请入口不随抽奖结束过期，结束后只供入群，不计入下次抽奖。
活跃加成（人品）按北京时间自然日计算，与发言或邀请门槛独立。
门槛未完成时加成暂存，有效抽奖次数为0；全部门槛和成员等条件合格后生效。
新活动两类加成都不设每日或累计上限；有效邀请须入群满24小时并核验，同群同人只算一次。
历史活动按本场冻结的留存、比例和上限计算；仅开启邀请门槛也能领取邀请入口。
群内输入“人品”查本人活跃加成，“人品排行”查本场活跃加成次数排行，“人品规则”查规则。
查询只读，不会自动报名或增加发言数；人品不是永久积分，无需兑换或扣分。

🏆 开奖与领奖
新建活动按北京时间定时开奖，历史活动沿用已保存的开奖规则。
在“我参加的抽奖 → 已结束”查看本人结果；中奖后按通知联系发奖人员。
资格暂时无法核验时会显示待核验，请按提示处理，不代表已经参与成功。"""


def guide_keyboard(links: HelpLinks) -> InlineKeyboardMarkup:
    return style_keyboard(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="创建抽奖", callback_data="nav:create"),
                    InlineKeyboardButton(text="我参加的抽奖", callback_data="nav:join"),
                ],
                *links.rows(),
                [InlineKeyboardButton(text="↩ 返回首页", callback_data="nav:index")],
            ]
        )
    )


GUIDE_TOPICS = (
    ("发起抽奖", "create"),
    ("参加与条件", "member"),
    ("邀请与活跃", "bonus"),
    ("开奖与领奖", "results"),
)


def guide_summary(links):
    rows = columns(
        [
            InlineKeyboardButton(text=label, callback_data="nav:guide:" + key)
            for label, key in GUIDE_TOPICS
        ]
    )
    return "📖 使用指南\n\n选择你正在办理的事项。", style_keyboard(
        InlineKeyboardMarkup(
            inline_keyboard=[
                *rows,
                *links.rows(),
                [InlineKeyboardButton(text="返回首页", callback_data="nav:index")],
            ]
        )
    )


def guide_topic(key: str, *, bot_username: str):
    markers = {
        "create": "➕ 发起抽奖",
        "member": "🛂 参加抽奖",
        "bonus": "📈 邀请与活跃加成",
        "results": "🏆 开奖与领奖",
    }
    marker = markers[key]
    start = GUIDE_TEXT.index(marker)
    end = min(
        (GUIDE_TEXT.index(value) for value in markers.values() if GUIDE_TEXT.index(value) > start),
        default=len(GUIDE_TEXT),
    )
    text = GUIDE_TEXT[start:end].strip()
    rows = []
    if key == "create":
        rows = [
            [
                InlineKeyboardButton(
                    text="添加到群组", url=f"https://t.me/{bot_username}?startgroup=lottery"
                )
            ],
            [
                InlineKeyboardButton(
                    text="添加到频道", url=f"https://t.me/{bot_username}?startchannel=lottery"
                )
            ],
            [InlineKeyboardButton(text="创建/继续草稿", callback_data="nav:create")],
        ]
    if key == "create":
        rows = [rows[0] + rows[1], rows[2]]
    rows.append([InlineKeyboardButton(text="返回使用指南", callback_data="nav:help")])
    return text, style_keyboard(InlineKeyboardMarkup(inline_keyboard=rows))
