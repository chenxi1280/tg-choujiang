from dataclasses import replace

from tg_lottery.application.bot_relationships import relationship_observation
from tg_lottery.domain.enums import QualificationState


async def refresh_relationship_result(session, campaign, result, *, user_id):
    policy = campaign.rules.get("platform_policy")
    if not policy:
        return result
    current = await relationship_observation(session, policy["bot_id"], user_id=user_id, lock=True)
    if current == result.relationship:
        return result
    state = (
        QualificationState.INELIGIBLE
        if current["state"] == "ineligible"
        else QualificationState.UNKNOWN
    )
    return replace(
        result,
        state=state,
        relationship=current,
        observed_status="bot_relationship_changed_during_observation",
    )
