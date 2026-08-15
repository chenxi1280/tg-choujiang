from tg_lottery.application.activity import MessageObservation
from tg_lottery.application.message_rules import (
    MIN_THRESHOLD_TEXT_LENGTH,
    normalize_text,
    rejection_reason,
)


def _obs(text, **kw):
    base = dict(
        chat_id=1,
        message_id=1,
        user_id=1,
        event_at=None,
        text=text,
        is_bot=False,
        is_service=False,
        is_forwarded=False,
    )
    return MessageObservation(**(base | kw))


def test_normalize_collapses_whitespace_and_case():
    assert normalize_text("  Hello   World ") == "hello world"


def test_threshold_length_is_four():
    assert MIN_THRESHOLD_TEXT_LENGTH == 4


def test_rejection_reason_matrix():
    assert rejection_reason(_obs("x", is_bot=True), "x", 4) == "bot"
    assert rejection_reason(_obs("x", is_service=True), "x", 4) == "service"
    assert rejection_reason(_obs("x", is_forwarded=True), "x", 4) == "forwarded"
    assert rejection_reason(_obs("/getid"), "/getid", 4) == "command"
    assert rejection_reason(_obs("abc"), "abc", 4) == "too_short"
    assert rejection_reason(_obs("🎉🎉🎉🎉"), "🎉🎉🎉🎉", 4) == "emoji_or_symbols_only"
    assert rejection_reason(_obs("a🎉🎉🎉"), "a🎉🎉🎉", 4) == "too_short"
    assert rejection_reason(_obs("这是有效发言"), "这是有效发言", 4) is None
