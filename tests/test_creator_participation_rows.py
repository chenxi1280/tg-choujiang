from tg_lottery.telegram.creator_participation import (
    apply_threshold,
    participation_rows,
    participation_text,
)


def _chat(chat_id: int, chat_type: str = "supergroup") -> dict:
    return {"id": chat_id, "title": f"条件 {chat_id}", "type": chat_type, "private": True}


def test_threshold_rows_show_current_value_and_match_chat_id() -> None:
    data = {
        "required_chats": [_chat(-100123), _chat(-100456)],
        "chat_message_thresholds": {"-100123": 10},
    }

    rows = participation_rows(data)

    assert [
        ("发言门槛 \u2212", "draft_chat_threshold:-100123:dec"),
        ("自定义 · 10 条", "draft_threshold_input:-100123"),
        ("\uff0b", "draft_chat_threshold:-100123:inc"),
    ] in rows
    assert [
        ("发言门槛 \u2212", "draft_chat_threshold:-100456:dec"),
        ("自定义条数", "draft_threshold_input:-100456"),
        ("\uff0b", "draft_chat_threshold:-100456:inc"),
    ] in rows
    assert rows[-1] == [("↩ 返回上一层", "draft_back")]


def test_channel_conditions_have_no_threshold_rows_or_hints() -> None:
    data = {
        "required_chats": [_chat(-100789, "channel")],
        "chat_message_thresholds": {"-100789": 10},
    }

    rows = participation_rows(data)
    text = participation_text(data)

    assert not any(button[1].startswith("draft_chat_threshold:") for row in rows for button in row)
    assert "发言 ≥" not in text
    assert "关注频道 条件 -100789" in text


def test_text_appends_threshold_hint_for_group_conditions() -> None:
    data = {
        "required_chats": [_chat(-100123)],
        "chat_message_thresholds": {"-100123": 10},
    }

    text = participation_text(data)

    assert "1. 加入群聊 条件 -100123 · 私有\uff08发言 ≥ 10 条\uff09" in text


def test_threshold_rows_follow_participation_pagination() -> None:
    chats = [_chat(-1000 - index) for index in range(1, 13)]
    data = {"required_chats": chats, "participation_page": 1}

    rows = participation_rows(data)

    threshold_ids = {
        button[1].split(":")[1]
        for row in rows
        for button in row
        if button[1].startswith("draft_chat_threshold:")
    }
    assert threshold_ids == {"-1011", "-1012"}


def test_apply_threshold_implements_inc_dec_and_noop() -> None:
    data = {"chat_message_thresholds": {"-100123": 2}}

    assert apply_threshold(data, "draft_chat_threshold:-100123:inc")["chat_message_thresholds"] == {
        "-100123": 3
    }
    assert apply_threshold(data, "draft_chat_threshold:-100123:noop")[
        "chat_message_thresholds"
    ] == {"-100123": 2}
    assert apply_threshold(data, "draft_chat_threshold:-100123:dec")["chat_message_thresholds"] == {
        "-100123": 1
    }
    at_one = apply_threshold(data, "draft_chat_threshold:-100123:dec")
    assert (
        apply_threshold(at_one, "draft_chat_threshold:-100123:dec")["chat_message_thresholds"] == {}
    )
    assert data["chat_message_thresholds"] == {"-100123": 2}
