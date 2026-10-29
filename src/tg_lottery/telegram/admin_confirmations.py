from tg_lottery.domain.enums import CampaignState
from tg_lottery.domain.errors import UserFacingError

CONFIRMED_ACTIONS = frozenset(
    {"cancel", "early_cutoff", "draw", "mark_claimed", "retry_publication", "retry_notices"}
)
ACTION_LABELS = {
    "retry_notices": "重试失败通知",
    "cancel": "取消活动",
    "early_cutoff": "提前截止",
    "draw": "立即开奖",
    "mark_claimed": "登记已领奖",
    "retry_publication": "重试明确失败项",
}
ACTION_IMPACTS = {
    "retry_notices": "只重试明确未发送的频道个人入口或开始通知，保留主卡、已确认页及未知投递。",
    "cancel": "本场停止后续参与和计奖，既有记录保留。在途或未知投递仍需核查，不代表消息没有发出。",
    "early_cutoff": (
        "将按当前北京时间提前截止并进入结算；资格复核完成后才能开奖，不会直接生成结果。"
    ),
    "draw": "将按已冻结名单和规则生成本场结果；群公告与中奖私信分别投递，不能重复开奖。",
    "mark_claimed": "仅记录该中奖名额已领奖，不代表机器人已交付奖品。请先确认实际领取情况。",
    "retry_publication": "只重试已判定远端未接收的明确失败项，不重发已确认或结果未知的投递。",
}


def confirmation_text(campaign, action: str, *, details: dict | None = None) -> str:
    text = f"确认{ACTION_LABELS[action]}\n\n活动：{campaign.name}\n{ACTION_IMPACTS[action]}"
    if action == "mark_claimed":
        text += (
            f"\n中奖者：{details['winner_name']}（{details['winner_user_id']}）"
            f"\n奖品：{details['prize_name']} · 名额{details['slot']}"
        )
    if campaign.state is CampaignState.PUBLISHING:
        text += "\n发布仍在处理中；本地活动状态与远端消息结果会分别保留。"
    return text


def validate_confirmation(campaign, payload: dict) -> None:
    if str(campaign.state) != payload["expected_state"]:
        raise UserFacingError("活动状态已变化，原确认未执行，请返回当前管理页重新核对")
    if campaign.config_revision != payload["expected_revision"]:
        raise UserFacingError("活动版本已变化，原确认未执行，请重新核对当前规则")


def validate_available_action(campaign, action):
    allowed = {
        "retry_notices": {CampaignState.SCHEDULED, CampaignState.RUNNING, CampaignState.PAUSED},
        "cancel": {
            CampaignState.DRAFT,
            CampaignState.PUBLISHING,
            CampaignState.SCHEDULED,
            CampaignState.RUNNING,
            CampaignState.PAUSED,
        },
        "early_cutoff": {CampaignState.RUNNING, CampaignState.PAUSED},
        "draw": {CampaignState.READY_TO_DRAW},
        "mark_claimed": {CampaignState.DRAWN},
        "retry_publication": {CampaignState.PUBLISHING},
    }
    if campaign.state not in allowed[action]:
        raise UserFacingError("当前活动状态不允许此操作，请返回活动管理核对")
