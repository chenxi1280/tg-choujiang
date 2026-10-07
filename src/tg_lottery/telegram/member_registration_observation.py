from tg_lottery.application.participants import QualificationResult
from tg_lottery.application.records import utcnow
from tg_lottery.domain.rules import CampaignRules
from tg_lottery.telegram import member_condition_views
from tg_lottery.telegram.member_observation import apply_message_thresholds
from tg_lottery.telegram.platform_qualification import observe_campaign_memberships
from tg_lottery.telegram.qualification import with_pending_approvals


async def complete_observation(
    flow,
    attempt,
    *,
    user_id: int,
    main_chat_id: int,
    campaign: object,
):
    chat_ids = tuple(dict.fromkeys((main_chat_id, *attempt.required_chat_ids)))
    observed_at = utcnow()
    rules = CampaignRules.model_validate(campaign.rules)
    result = await observe_campaign_memberships(
        flow._bot,
        rules=rules,
        chat_ids=chat_ids,
        user_id=user_id,
        observed_at=observed_at,
        relationship_loader=flow._relationships.observation if flow._relationships else None,
    )
    pending_chat_ids = await flow._participants.pending_approval_chat_ids(attempt.participant_id)
    thresholds = {int(key): int(value) for key, value in rules.message_thresholds.items()}
    observations = member_condition_views.with_target_kinds(
        with_pending_approvals(result.observations, pending_chat_ids),
        attempt.target_kinds,
    )
    state, observations = await apply_message_thresholds(
        flow._stats,
        result.state,
        observations,
        attempt=attempt,
        user_id=user_id,
        observed_at=observed_at,
        thresholds=thresholds,
    )
    participant = await flow._participants.complete_registration(
        QualificationResult(
            participant_id=attempt.participant_id,
            qualification_check_id=attempt.qualification_check_id,
            state=state,
            observed_at=observed_at,
            observed_status=result.observed_status,
            error_code=result.error_code,
            observations=observations,
            relationship=result.relationship,
            profile=result.profile,
        )
    )
    return participant.qualification_state, observations, participant.ticket_total_cache
