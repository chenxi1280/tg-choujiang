"""Editable defaults for new drafts; never interpret description as executable policy."""

DEFAULT_DESCRIPTION = "\n".join(
    (
        "📋 参与须知",
        "1. 🎟️ 请先报名，并在截止前完成本场全部参与条件；未达门槛不能获得奖励。",
        "2. 👥 开奖时须仍满足入群及关注条件；退群、取消必需关注或资格复核未通过，参与无效。",
        "3. 💬 前置条件通过且报名成功后，在本场允许的计数时段内统计任务，此前记录不补算。",
        "4. 🎁 中奖后请按领奖说明联系主办方领取奖励。",
        "5. 📌 领奖后7天内请勿退出本场要求加入的群；期间退群，主办方可收回奖励。",
    )
)
UX_POLICY_VERSION = "r14"
