from tg_lottery.application.admission_qualification import behavior_qualified
from tg_lottery.application.bot_relationships import relationship_observation
from tg_lottery.application.draw_profile import current_draw_profile
from tg_lottery.application.operational_blocks import project_qualification_blocks
from tg_lottery.application.participant_qualification import (
    build_draw_check,
    store_target_observations,
)
from tg_lottery.domain.enums import QualificationState
from tg_lottery.infrastructure.db import Campaign, Participant


async def record_draw_qualification(
    uow_factory,
    *,
    participant_id,
    state,
    observed_at,
    observed_status,
    error_code,
    observations=(),
    relationship=None,
    review_id=None,
    profile=None,
):
    async with uow_factory() as uow:
        participant = await uow.session.get(Participant, participant_id)
        if participant is None:
            raise ValueError("participant not found")
        campaign = await uow.session.get(Campaign, participant.campaign_id)
        state, profile = await current_draw_profile(
            uow.session, campaign, user_id=participant.user_id, state=state, profile=profile
        )
        policy = campaign.rules.get("platform_policy")
        excluded = error_code == "behavior_gate_incomplete" and not behavior_qualified(
            participant, campaign
        )
        state, relationship = await _current_relationship(
            uow.session,
            participant,
            policy=policy,
            excluded=excluded,
            state=state,
            relationship=relationship,
        )
        participant = await uow.session.get(
            Participant, participant_id, with_for_update=True, populate_existing=True
        )
        return await _store_check(
            uow.session,
            participant,
            state=state,
            observed_at=observed_at,
            observed_status=observed_status,
            error_code=error_code,
            observations=observations,
            relationship=relationship,
            review_id=review_id,
            profile=profile,
            excluded=excluded,
        )


async def _persist_observations(session, check, observations):
    from tg_lottery.application.participants import QualificationResult

    result = QualificationResult(
        participant_id=check.participant_id,
        qualification_check_id=check.id,
        state=check.state,
        observed_at=check.observed_at,
        observations=observations,
    )
    store_target_observations(session, check, result)
    participant = await session.get(Participant, check.participant_id)
    await project_qualification_blocks(
        session, campaign_id=participant.campaign_id, observations=observations
    )


async def _current_relationship(session, participant, *, policy, excluded, state, relationship):
    if policy is None or excluded:
        return state, relationship
    current = await relationship_observation(
        session, policy["bot_id"], user_id=participant.user_id, lock=True
    )
    if current != relationship:
        state = (
            QualificationState.INELIGIBLE
            if current["state"] == "ineligible"
            else QualificationState.UNKNOWN
        )
    return state, current


def _annotate_check(check, participant, *, relationship, review_id, profile, excluded, state):
    if relationship is not None:
        check.observations["bot_relation"] = relationship
    if review_id is not None:
        check.observations["platform_review_id"] = review_id
    if profile:
        check.observations["profile"] = profile
    if excluded:
        check.participation_groups_state = QualificationState.UNKNOWN
        check.requirements_state = QualificationState.UNKNOWN
    else:
        participant.qualification_state = state


async def _store_check(
    session,
    participant,
    *,
    state,
    observed_at,
    observed_status,
    error_code,
    observations,
    relationship,
    review_id,
    profile,
    excluded,
):
    if profile.get("region_state") == "ineligible" and error_code is None:
        error_code, observed_status = "region_not_allowed", "profile:region_not_allowed"
    check = build_draw_check(
        participant,
        state=state,
        observed_at=observed_at,
        observed_status=observed_status,
        error_code=error_code,
        observations=observations,
    )
    _annotate_check(
        check,
        participant,
        relationship=relationship,
        review_id=review_id,
        profile=profile,
        excluded=excluded,
        state=state,
    )
    session.add(check)
    await session.flush()
    await _persist_observations(session, check, observations)
    return check
