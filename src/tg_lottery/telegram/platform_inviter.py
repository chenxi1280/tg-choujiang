from tg_lottery.application.records import utcnow
from tg_lottery.domain.enums import QualificationState
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.member_registration import registration_request
from tg_lottery.telegram.qualification import QualificationRetryable


async def verify_platform_inviter(flow, campaign, *, user):
    if not campaign.rules.get("platform_policy"):
        return
    detail = await flow._queries.participant_campaign_detail(user.id, campaign.public_ref)
    if detail is None or not detail.registration_completed:
        raise UserFacingError("请先在本场明确登记；获取邀请入口不会自动报名")
    attempt = await flow._participants.begin_registration(
        registration_request(campaign.id, user, utcnow())
    )
    group = await flow._queries.campaign_group(campaign.id)
    try:
        state, _, _ = await flow._complete_observation(
            attempt, user_id=user.id, main_chat_id=group.chat_id, campaign=campaign
        )
    except QualificationRetryable as exc:
        raise UserFacingError(
            "本人平台条件暂时无法核验，请稍后从本场详情验证后再获取邀请入口"
        ) from exc
    if state is not QualificationState.ELIGIBLE:
        raise UserFacingError("本人平台或成员条件尚未通过，请从本场详情完成条件并验证")
