"""成员流程弹窗文案: 报名失败原因的用户提示。"""

from tg_lottery.domain.enums import QualificationState


def failure_reply_text(campaign: object, state: QualificationState) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    tips = presentation.get("message_tips", True)
    if state is QualificationState.INELIGIBLE:
        return "尚未满足全部群/频道参与条件。" + (" 完成后请重新验证。" if tips else "")
    retry = " 请稍后点击验证资格重试。"
    return "当前无法核验群/频道资格，状态保持待核验。" + (retry if tips else "")


def popup_failure(campaign: object, observations: tuple[dict[str, object], ...]) -> str:
    presentation = getattr(campaign, "presentation", {}) or {}
    chats = [presentation.get("main_chat"), *(presentation.get("required_chats") or [])]
    titles = {
        int(chat["id"]): str(chat.get("title") or "群聊")
        for chat in chats
        if isinstance(chat, dict)
    }
    for item in observations:
        if item.get("error_code") == "message_threshold":
            chat_id = int(item["chat_id"])
            status = str(item["observed_status"]).removeprefix("messages ")
            have, _, need = status.partition("/")
            gap = int(need) - int(have)
            return f"「{titles.get(chat_id, '群聊')}」发言 {have}/{need} 条，还差 {gap} 条"
    for item in observations:
        if item.get("state") == "ineligible":
            return f"请先加入：{titles.get(int(item['chat_id']), '条件群聊')}"
    return "尚未满足全部参与条件"
