from dataclasses import replace

from tg_lottery.domain.enums import QualificationState


def with_username_check(result, *, rules, observed_at):
    if not rules.username_required:
        return result
    observed = [item for item in result.observations if item.get("profile_observed")]
    names = {item.get("username") for item in observed}
    if not names or len(names) != 1:
        state, code = QualificationState.UNKNOWN, "username_observation_unknown"
    elif None in names:
        state, code = QualificationState.INELIGIBLE, "username_missing"
    else:
        state, code = QualificationState.ELIGIBLE, None
    final = _combine(result.state, state)
    return replace(
        result,
        state=final,
        error_code=result.error_code or code,
        observed_status=f"profile:{code}" if code else result.observed_status,
        profile=_profile_snapshot(names, state=state, observed_at=observed_at),
    )


def _combine(membership, profile):
    if QualificationState.INELIGIBLE in {membership, profile}:
        return QualificationState.INELIGIBLE
    if QualificationState.UNKNOWN in {membership, profile}:
        return QualificationState.UNKNOWN
    return membership


def _profile_snapshot(names, *, state, observed_at):
    return {
        "username_state": state.value,
        "usernames": sorted(name or "" for name in names),
        "observed_at": observed_at.isoformat(),
    }
