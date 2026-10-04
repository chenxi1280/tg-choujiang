from datetime import timedelta

from tg_lottery.application.records import utcnow
from tg_lottery.domain.admission import validate_admission_window
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram.draft_config import rules_from_data


def validate_gate_time(data, *, draw_at=None):
    from datetime import datetime

    if not (data.get("admission") or {}).get("groups"):
        return
    saved_draw = draw_at or (
        datetime.fromisoformat(data["draw_at"]) if data.get("draw_at") else None
    )
    if saved_draw is None:
        return
    try:
        validate_admission_window(
            rules_from_data(data), start_at=utcnow(), cutoff_at=saved_draw - timedelta(seconds=300)
        )
    except ValueError as exc:
        raise UserFacingError(str(exc)) from exc
