import logging

from tg_lottery.application.request_context import bind_update_context
from tg_lottery.runtime import ContextFormatter


def test_context_formatter_emits_update_delivery_and_release_ids(monkeypatch) -> None:
    monkeypatch.setenv("RELEASE_SHA", "abc123")
    monkeypatch.setenv("RELEASE_ID", "release-1")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="delivered",
        args=(),
        exc_info=None,
    )
    record.outbox_id = "outbox-1"
    record.certainty = "confirmed"
    formatter = ContextFormatter("%(levelname)s %(message)s")
    with bind_update_context(123, 456):
        rendered = formatter.format(record)
    assert "bot_id=123" in rendered
    assert "update_id=456" in rendered
    assert "outbox_id=outbox-1" in rendered
    assert "certainty=confirmed" in rendered
    assert "release_sha=abc123" in rendered
    assert "release_id=release-1" in rendered
