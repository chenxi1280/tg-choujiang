import pytest

from tests.v30_helpers import FIRST, SECOND
from tg_lottery.domain.errors import UserFacingError
from tg_lottery.telegram import creator_behavior_editor as editor
from tg_lottery.telegram import creator_behavior_views as views
from tg_lottery.telegram.draft_config import new_draft


def _draft():
    data = new_draft() | {
        "participation_groups": [{"id": FIRST, "title": "A群"}, {"id": SECOND, "title": "B群"}]
    }
    return editor.apply_action(editor.open_editor(data), "draft_gate:mode:message")


def test_no_search_matches_explains_result_and_can_restore_all_groups():
    data = editor.apply_action(_draft(), "draft_gate:search")
    data = editor.receive(data, "不存在的群")
    text, rows = views.view(data)
    assert "没有匹配" in text
    action = next(action for row in rows for label, action in row if label == "清除搜索")

    cleared = editor.apply_action(data, action)

    assert cleared[editor.EDITOR_KEY]["search"] == ""
    assert cleared[editor.EDITOR_KEY]["type"] == "message"
    _, rows = views.view(cleared)
    assert any(action == f"draft_gate:group:{FIRST}" for row in rows for _, action in row)
    assert any(action == f"draft_gate:group:{SECOND}" for row in rows for _, action in row)


def test_switching_mode_resets_previous_mode_search():
    data = editor.apply_action(_draft(), "draft_gate:search")
    data = editor.receive(data, "不存在的群")
    switched = editor.apply_action(data, "draft_gate:mode:invite")
    assert switched[editor.EDITOR_KEY]["search"] == ""
    _, rows = views.view(switched)
    assert any(action == f"draft_gate:group:{FIRST}" for row in rows for _, action in row)


@pytest.mark.parametrize("target_count", [0, views.PAGE_SIZE])
def test_copy_pages_count_only_other_groups(target_count):
    data = _draft() | {
        "participation_groups": [
            {"id": FIRST, "title": "来源群"},
            *({"id": SECOND - index, "title": f"目标群{index}"} for index in range(target_count)),
        ]
    }
    data = editor.apply_action(data, f"draft_gate:group:{FIRST}")
    data = editor.apply_action(data, "draft_gate:copy_open")
    text, rows = views.view(data)
    actions = [action for row in rows for _, action in row]
    assert "draft_gate:page:1" not in actions
    if not target_count:
        assert "没有其他参与群可复制" in text
        assert "draft_gate:copy_cancel" in actions


def test_copy_requires_selected_target_and_preserves_staged_source():
    data = editor.apply_action(_draft(), f"draft_gate:group:{FIRST}")
    data = editor.apply_action(data, "draft_gate:copy_open")
    with pytest.raises(UserFacingError, match="请先选择要复制的目标群"):
        editor.apply_action(data, "draft_gate:copy_confirm")
    assert data[editor.EDITOR_KEY]["copy_targets"] == []
    assert data[editor.EDITOR_KEY]["rule"]["daily_messages"] == 5
