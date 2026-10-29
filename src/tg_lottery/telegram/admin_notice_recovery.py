from tg_lottery.application.delivery_purposes import PURPOSE_LABELS
from tg_lottery.application.notice_recovery import NOTICE_OPERATIONS, NOTICE_STATES
from tg_lottery.application.records import utcnow
from tg_lottery.domain.action_confirmation import ExpectedCampaign


def notice_actions(campaign, deliveries):
    if campaign.state not in NOTICE_STATES or campaign.cutoff_at <= utcnow():
        return ()
    labels = {PURPOSE_LABELS[op] for op in NOTICE_OPERATIONS}
    return (
        ("retry_notices",)
        if any(row.label in labels and row.failed for row in deliveries.purposes)
        else ()
    )


async def retry_notices(flow, query, grant):
    await flow._verify_admin(grant.campaign_id, query.from_user.id)
    expected = ExpectedCampaign(
        state=grant.payload["expected_state"], revision=grant.payload["expected_revision"]
    )
    count = await flow._campaigns.retry_failed_notices(
        grant.campaign_id, query.from_user.id, expected=expected
    )
    await query.message.answer(f"已将 {count} 条明确失败通知重新排队；主卡与未知投递保持原状态。")
