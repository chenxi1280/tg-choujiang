from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import UserProfile


async def current_draw_profile(session, campaign, *, user_id, state, profile):
    profile = dict(profile or {})
    regions = campaign.rules.get("allowed_regions") or []
    if regions:
        user = await session.get(UserProfile, user_id, with_for_update=True)
        region = user.region if user else None
        profile["region"] = region
        if region not in regions:
            state = QualificationState.INELIGIBLE
            profile["region_state"] = "ineligible"
        else:
            profile["region_state"] = "eligible"
    return state, profile
