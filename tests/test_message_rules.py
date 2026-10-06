import pytest

from tg_lottery.application.activity import MessageObservation
from tg_lottery.application.message_rules import (
    normalize_text,
    normalized_content,
    rejection_reason,
)


def _obs(text, **kw):
    base = dict(chat_id=1, message_id=1, user_id=1, event_at=None, text=text)
    return MessageObservation(**(base | kw))


def test_normalize_collapses_whitespace_and_case():
    assert normalize_text("  Hello   World ") == "hello world"


@pytest.mark.parametrize(
    ("text", "flags", "reason"),
    [
        ("x", {"is_bot": True}, "bot"),
        ("x", {"is_service": True}, "service"),
        ("x", {"is_forwarded": True}, "forwarded"),
        ("/getid", {}, "command"),
        ("/start", {"media_key": "photo:one"}, "command"),
        (" ", {}, "empty"),
        (None, {}, "empty"),
    ],
)
def test_rejection_reason_matrix(text, flags, reason):
    assert rejection_reason(_obs(text, **flags)) == reason


@pytest.mark.parametrize("text", ["短", "a", "🎉", "a🎉", "!!!", "这是有效发言"])
def test_any_nonempty_text_counts(text):
    assert rejection_reason(_obs(text)) is None


def test_media_content_uses_stable_identity_and_caption():
    photo = _obs(None, media_key="photo:one")
    assert rejection_reason(photo) is None
    assert normalized_content(photo) == normalized_content(_obs(" ", media_key="photo:one"))
    assert normalized_content(photo) != normalized_content(_obs(None, media_key="photo:two"))
    assert normalized_content(photo) != normalized_content(_obs(None, media_key="sticker:one"))
    assert normalized_content(photo) != normalized_content(_obs("说明", media_key="photo:one"))
    assert normalized_content(_obs(" HELLO ", media_key="photo:one")) == normalized_content(
        _obs("hello", media_key="photo:one")
    )
