PRESET_PREFIX = "draft_bonus_preset:"


def number_row(label: str, action: str, value: object) -> list[tuple[str, str]]:
    field = "cap" if "cap" in action else "cycle"
    unit = (
        "轮"
        if action == "activity_cap"
        else "次"
        if field == "cap"
        else "人"
        if action.startswith("invite")
        else "条"
    )
    return [
        (f"{label} −1{unit}", f"draft_bonus_{action}_dec"),
        (f"自定义 · {value}{unit}", f"draft_bonus_input_{field}"),
        (f"＋1{unit}", f"draft_bonus_{action}_inc"),
    ]


def mode_row(kind: str, mode: str) -> list[tuple[str, str]]:
    unlimited = "✅ 不设上限" if mode == "unlimited" else "不设上限"
    limited = "✅ 设置上限" if mode == "limited" else "设置上限"
    return [
        (unlimited, f"draft_bonus_{kind}_unlimited"),
        (limited, f"draft_bonus_{kind}_limited"),
    ]


def preset_row(
    field: str, values: tuple[int, ...], unit: str, *, selected=None
) -> list[tuple[str, str]]:
    return [
        (("✅ " if value == selected else "") + f"{value}{unit}", f"{PRESET_PREFIX}{field}:{value}")
        for value in values
    ]
