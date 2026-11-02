"""成员流程弹窗文案: 报名失败原因的用户提示。"""

from tg_lottery.domain.enums import QualificationState
from tg_lottery.telegram.bonus_rule_text import chat_title


def failure_reply_text(campaign: object, state: QualificationState) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    tips = presentation.get("message_tips", True)
    if state is QualificationState.INELIGIBLE:
        return "尚未满足全部群/频道参与条件。" + (" 完成后请重新验证。" if tips else "")
    return "暂时查不到你的入群/关注状态，请稍后点「重新检查」。"


def popup_success(total: int) -> str:
    return f"✅ 报名成功！有效抽奖次数：{total}"


def popup_already_participated(total: int) -> str:
    return f"你已报名，当前有效抽奖次数：{total}"


def popup_failure(
    campaign: object,
    state: QualificationState,
    observations: tuple[dict[str, object], ...],
) -> str:
    if state is QualificationState.UNKNOWN:
        return "暂时查不到你的入群/关注状态，请稍后点「重新检查」。"
    presentation = getattr(campaign, "presentation", {}) or {}
    titles = _condition_titles(presentation)
    threshold = _threshold_shortfall_text(titles, observations)
    if threshold:
        return threshold
    missing = [
        titles.get(int(item["chat_id"]), "条件群聊")
        for item in observations
        if item.get("state") == "ineligible"
    ]
    if missing:
        return _missing_groups_text(missing)
    return "尚未满足全部参与条件"


def _condition_titles(presentation: dict[str, object]) -> dict[int, str]:
    chats = [
        presentation.get("main_chat"),
        *(presentation.get("participation_groups") or []),
        *(presentation.get("required_chats") or []),
    ]
    return {int(chat["id"]): chat_title(chat) for chat in chats if isinstance(chat, dict)}


def _threshold_shortfall_text(
    titles: dict[int, str], observations: tuple[dict[str, object], ...]
) -> str | None:
    for item in observations:
        if item.get("error_code") == "message_threshold":
            chat_id = int(item["chat_id"])
            status = str(item["observed_status"]).removeprefix("messages ")
            have, _, need = status.partition("/")
            gap = int(need) - int(have)
            return f"「{titles.get(chat_id, '群聊')}」发言 {have}/{need} 条，还差 {gap} 条"
    return None


def _missing_groups_text(missing: list[str]) -> str:
    names = "、".join(missing[:2])
    if len(missing) > 2:
        names += f"等 {len(missing)} 个群/频道"
    return f"请先加入群聊或关注频道：{names}"
