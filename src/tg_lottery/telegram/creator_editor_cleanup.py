def retain_group_editors(data: dict, selected: set[str]) -> dict:
    result = dict(data)
    admission = data.get("admission_editor")
    if admission:
        result["admission_editor"] = _retained_admission_editor(admission, selected)
    working = data.get("bonus_working_copy")
    if working and str(working["chat_id"]) not in selected:
        result = {
            key: value
            for key, value in result.items()
            if key not in {"bonus_working_copy", "bonus_input_field"}
        }
    copying = data.get("bonus_copy")
    if copying:
        result = _retained_bonus_copy(result, copying, selected)
    return result


def _retained_admission_editor(editor: dict, selected: set[str]) -> dict:
    result = editor | {
        "groups": {key: value for key, value in editor["groups"].items() if key in selected}
    }
    if str(editor.get("chat_id")) not in selected:
        result = {
            key: value for key, value in result.items() if key not in {"rule", "pending_action"}
        }
        result = result | {"chat_id": None, "field": None}
    if "copy_targets" in result:
        result = result | {
            "copy_targets": [key for key in result["copy_targets"] if key in selected]
        }
    return result


def _retained_bonus_copy(data: dict, copying: dict, selected: set[str]) -> dict:
    if str(copying["source_chat_id"]) not in selected:
        return {key: value for key, value in data.items() if key != "bonus_copy"}
    targets = [key for key in copying["targets"] if str(key) in selected]
    return data | {"bonus_copy": copying | {"targets": targets, "confirm": False}}


def has_group_edits(data: dict, chat_id: int) -> bool:
    key = str(chat_id)
    editor = data.get("admission_editor") or {}
    working = data.get("bonus_working_copy") or {}
    copying = data.get("bonus_copy") or {}
    return (
        key in editor.get("groups", {})
        or str(editor.get("chat_id")) == key
        or str(working.get("chat_id")) == key
        or str(copying.get("source_chat_id")) == key
        or chat_id in copying.get("targets", [])
    )
