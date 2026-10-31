from tg_lottery.domain.rules import CampaignRules


def invitation_purpose(rules: CampaignRules, chat_id: int) -> str:
    gate = bool(
        rules.admission
        and rules.admission.type == "invite"
        and str(chat_id) in rules.admission.groups
    )
    bonus = rules.invite_bonus_enabled and rules.invite_rule_for(chat_id) is not None
    if gate and bonus:
        return "完成邀请门槛并增加邀请加成票"
    return "完成邀请门槛" if gate else "增加邀请加成票"
