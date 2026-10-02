from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_lottery.domain.draft_contract import DRAFT_SESSION_TTL_HOURS

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
        return [[InlineKeyboardButton(text=title, url=url)] for title, url in links if url]


GUIDE_TEXT = f"""📖 抽奖机器人使用指南

🎁 发起抽奖
将机器人加入参与群和条件频道并设为管理员。私有群/频道需开启邀请用户权限。
点击“创建抽奖”，选择参与群，再设置标题、奖品和开奖时间。
在“准入条件”添加需要关注的频道或加入的群；可添加多个，参与者必须全部满足。
在“抽奖加成”分别设置各群邀请或发言奖励。发布前核对预览中的名称、条件和时间。
未发布草稿在最后一次更新后保留 {DRAFT_SESSION_TTL_HOURS} 小时；发送 /new 可继续有效草稿。

🎫 参加抽奖
点击活动卡“立即参与”，按提示关注所有频道、加入所有群，再点击“验证资格”。
加入申请需等待管理员批准。发言门槛、用户名或地区要求以本次活动规则为准。
“我参加的抽奖”可查看报名状态、抽奖次数和结果。

👥 邀请与发言加成
报名后进入“我的加成”，选择邀请加成和目标群，复制本期邀请链接分享。
每次抽奖、每个群分别累计；有效邀请完成留存与核验后自动计入，无需手动领取。
点击链接、已经在群内或未完成核验均不代表获得加成。
本期新生成的邀请入口不随抽奖结束过期，结束后只供入群，不计入下次抽奖。
发言加成是可选奖励，与必须满足的报名发言门槛分开计算。

🏆 开奖与领奖
新建活动按北京时间定时开奖，历史活动沿用已保存的开奖规则。
在“我参加的抽奖 → 已结束”查看本人结果；中奖后按通知联系发奖人员。
资格暂时无法核验时会显示待核验，请按提示处理，不代表已经参与成功。"""


def guide_keyboard(links: HelpLinks) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 创建抽奖", callback_data="nav:create")],
            [InlineKeyboardButton(text="🎫 我参加的抽奖", callback_data="nav:join")],
            *links.rows(),
            [InlineKeyboardButton(text="↩ 返回首页", callback_data="nav:index")],
        ]
    )
