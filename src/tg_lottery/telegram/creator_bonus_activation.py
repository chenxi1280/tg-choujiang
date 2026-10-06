from tg_lottery.telegram.bonus_rule_text import chat_title


def save_label(data: dict, kind: str) -> str:
    return "保存本群设置" if kind in data.get("bonus_types", []) else "保存并开启本加成"


def activation_notice(data: dict, kind: str, *, chat_id: int) -> str:
    if kind in data.get("bonus_types", []):
        return "\n\n保存仅更新本群设置。"
    rules = data.get(f"group_{kind}_rules") or {}
    targets = {str(key) for key, rule in rules.items() if rule.get("enabled")}
    targets.add(str(chat_id))
    names = [
        chat_title(group)
        for group in data.get("participation_groups", [])
        if str(group["id"]) in targets
    ]
    return "\n\n保存将开启本加成，并恢复这些群的已保存规则：\n" + "、".join(names)
