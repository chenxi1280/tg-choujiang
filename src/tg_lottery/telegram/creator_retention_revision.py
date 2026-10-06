from tg_lottery.domain.errors import UserFacingError

EDITOR = "admission_editor"


def synchronize_retention(data: dict) -> dict:
    if EDITOR not in data:
        return dict(data)
    editor = dict(data[EDITOR])
    current = data.get("invite_retention_hours")
    base = editor.get("retention_base", editor.get("retention_hours"))
    modified = (
        bool(editor.get("retention_modified", False)) or editor.get("retention_hours") != base
    )
    if "retention_base" not in editor and editor.get("retention_hours") != current:
        modified = True
    updated = _synchronize(editor, current=current, base=base, modified=modified)
    return data | {EDITOR: updated}


def _synchronize(editor: dict, *, current, base, modified: bool) -> dict:
    clean = {key: value for key, value in editor.items() if key != "retention_conflict"}
    if not modified:
        return clean | {
            "retention_hours": current,
            "retention_base": current,
            "retention_modified": False,
        }
    if current == base or current == editor.get("retention_hours"):
        return clean | {"retention_base": current, "retention_modified": True}
    return clean | {
        "retention_conflict": {
            "base": base,
            "current": current,
            "editing": editor.get("retention_hours"),
        }
    }


def checked_retention_editor(data: dict) -> dict:
    editor = synchronize_retention(data)[EDITOR]
    if editor.get("retention_conflict"):
        raise UserFacingError("共享邀请留存已在其他页面修改，请先选择保留哪一个值")
    return editor


def resolve_retention(data: dict, *, use_saved: bool) -> dict:
    editor = data[EDITOR]
    current = data.get("invite_retention_hours")
    value = current if use_saved else editor["retention_hours"]
    clean = {key: value for key, value in editor.items() if key != "retention_conflict"}
    return data | {
        EDITOR: clean
        | {"retention_hours": value, "retention_base": current, "retention_modified": not use_saved}
    }


def retention_conflict_view(data: dict):
    conflict = data[EDITOR]["retention_conflict"]
    text = (
        "共享邀请留存设置冲突\n\n"
        f"原编辑基线：{conflict['base']}小时\n"
        f"现在已保存：{conflict['current']}小时\n"
        f"本次待保存：{conflict['editing']}小时\n"
        "选择只解决留存值，其他门槛编辑保留；整场保存前仍会核验时间窗口。"
    )
    rows = [
        [("保留当前已保存值", "draft_gate:retention_saved")],
        [("保留本次编辑值", "draft_gate:retention_edited")],
    ]
    return text, rows
