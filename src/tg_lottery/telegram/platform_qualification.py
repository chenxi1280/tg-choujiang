from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.qualification import (
    QualificationObservationResult,
    SmoothRateLimiter,
    observe_memberships,
    preflight_membership_targets,
)
from tg_lottery.telegram.qualification_profile import with_username_check


async def observe_campaign_memberships(
    bot,
    *,
    rules,
    chat_ids,
    user_id,
    observed_at,
    relationship_loader,
    limiter=None,
    preflighted=False,
):
    limiter = limiter or SmoothRateLimiter(20)
    policy = rules.platform_policy
    if policy is None:
        result = await observe_memberships(
            bot, chat_ids=chat_ids, user_id=user_id, observed_at=observed_at, limiter=limiter
        )
        return with_username_check(result, rules=rules, observed_at=observed_at)
    if int(bot.id) != policy.bot_id:
        raise ValueError("frozen platform policy bot identity mismatch")
    failures = await _platform_preflight(bot, policy, limiter=limiter, preflighted=preflighted)
    unavailable = {item.chat_id: item.error_code for item in failures}
    result = await observe_memberships(
        bot,
        chat_ids=tuple(cid for cid in chat_ids if cid not in unavailable),
        user_id=user_id,
        observed_at=observed_at,
        limiter=limiter,
    )
    observations = _platform_observations(result, unavailable=unavailable, observed_at=observed_at)
    if relationship_loader is None:
        raise RuntimeError("platform qualification requires durable bot relationship evidence")
    relationship = await relationship_loader(user_id)
    states = {item["state"] for item in observations} | {relationship["state"]}
    state = combined_state(states)
    result = QualificationObservationResult(
        state,
        f"platform:{state.value}",
        "platform_target_unavailable" if failures else None,
        observations,
        relationship,
    )
    return with_username_check(result, rules=rules, observed_at=observed_at)


def _platform_observations(result, *, unavailable, observed_at):
    return result.observations + tuple(
        {
            "chat_id": cid,
            "state": "unknown",
            "observed_status": "platform_target_unavailable",
            "error_code": code,
            "observed_at": observed_at.isoformat(),
        }
        for cid, code in unavailable.items()
    )


async def _platform_preflight(bot, policy, *, limiter, preflighted):
    if preflighted:
        return ()
    return await preflight_membership_targets(
        bot, chat_ids=policy.chat_ids, admin_chat_ids=policy.chat_ids, limiter=limiter
    )


def combined_state(states):
    if "ineligible" in states:
        return QualificationState.INELIGIBLE
    if states.intersection({"unknown", "pending"}):
        return QualificationState.UNKNOWN
    return QualificationState.ELIGIBLE
