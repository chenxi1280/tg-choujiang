import datetime
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy/monitor_services.py"
SPEC = importlib.util.spec_from_file_location("service_monitor", SCRIPT)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)
NOW = datetime.datetime(2026, 11, 4, 10, tzinfo=datetime.UTC)


@pytest.fixture
def notifications(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "STATE_FILE", tmp_path / "state.json")
    sent = []
    monkeypatch.setattr(monitor, "send_feishu", sent.append)
    return sent


def _report(*issues):
    return {
        "issues": list(issues),
        "details": ["STATUS=DEGRADED_ACKNOWLEDGED unknown_deliveries=37"],
    }


def test_alert_dedup_change_and_recovery(notifications):
    assert monitor.process_report(_report("worker stale"), now=NOW) == 1
    assert monitor.process_report(_report("worker stale"), now=NOW) == 1
    assert len(notifications) == 1
    assert monitor.process_report(_report("new UNKNOWN"), now=NOW) == 1
    assert len(notifications) == 2
    assert "线上告警" in notifications[-1]
    assert monitor.process_report(_report(), now=NOW) == 0
    assert "告警恢复" in notifications[-1]
    assert "历史 UNKNOWN 仍未确认" in notifications[-1]
    assert monitor.process_report(_report(), now=NOW) == 0
    assert len(notifications) == 3


def test_log_error_aging_out_with_unreviewed_unknown_updates_alert_scope(notifications):
    historical = "UNKNOWN_UNREVIEWED outbox_id=historical operation=delete_message"
    polling_error = "tg-lottery-ops-bot-1 ERROR logger=aiogram.dispatcher"
    monitor.process_report(_report(historical), now=NOW)
    monitor.process_report(_report(historical, polling_error), now=NOW)
    assert "线上告警" in notifications[-1]

    assert monitor.process_report(_report(historical), now=NOW) == 1
    assert "告警范围缩小" in notifications[-1]
    assert historical in notifications[-1]
    assert polling_error not in notifications[-1]
    assert monitor.process_report(_report(historical), now=NOW) == 1
    assert len(notifications) == 3


def test_daily_report_once_per_beijing_day(notifications):
    monitor.process_report(_report(), now=NOW)
    assert "监控已启用" in notifications[0]
    nine = NOW.replace(day=5, hour=9)
    monitor.process_report(_report(), now=nine)
    monitor.process_report(_report(), now=nine.replace(minute=55))
    assert len(notifications) == 2
    assert "每日巡检正常" in notifications[-1]
    monitor.process_report(_report(), now=nine.replace(day=6))
    assert len(notifications) == 3


def test_failed_notification_does_not_acknowledge_alert_or_recovery(notifications, monkeypatch):
    monitor.process_report(_report(), now=NOW)
    original = monitor.STATE_FILE.read_text()

    def fail(_message):
        raise OSError("test sender failure")

    monkeypatch.setattr(monitor, "send_feishu", fail)
    with pytest.raises(OSError):
        monitor.process_report(_report("new UNKNOWN"), now=NOW)
    assert monitor.STATE_FILE.read_text() == original
    monkeypatch.setattr(monitor, "send_feishu", notifications.append)
    monitor.process_report(_report("new UNKNOWN"), now=NOW)
    alerted = monitor.STATE_FILE.read_text()
    monkeypatch.setattr(monitor, "send_feishu", fail)
    with pytest.raises(OSError):
        monitor.process_report(_report(), now=NOW)
    assert monitor.STATE_FILE.read_text() == alerted
    monkeypatch.setattr(monitor, "send_feishu", notifications.append)
    monitor.process_report(_report(), now=NOW)
    assert "告警恢复" in notifications[-1]


def test_runtime_failure_keeps_unknowns_visible_without_raw_errors(monkeypatch, capsys):
    output = (
        "STATUS=DEGRADED unknown_deliveries=38\n"
        "UNKNOWN_ACKNOWLEDGED outbox_id=historical\n"
        "UNKNOWN_UNREVIEWED outbox_id=new operation=delete_message\n"
        "Traceback: password=secret https://example.test/private\n"
    )
    monkeypatch.setattr(monitor, "run_command", lambda *a, **kw: (1, output))
    issues, details = [], []
    monitor.check_runtime(issues, details)
    assert "UNKNOWN_ACKNOWLEDGED outbox_id=historical" in capsys.readouterr().out
    assert "UNKNOWN_UNREVIEWED outbox_id=new operation=delete_message" in issues
    assert all("historical" not in item for item in issues)
    assert all("secret" not in item for item in [*issues, *details])


def test_acknowledged_unknown_alone_does_not_alert(monkeypatch):
    monkeypatch.setattr(
        monitor,
        "run_command",
        lambda *a, **kw: (0, "STATUS=DEGRADED_ACKNOWLEDGED unknown_deliveries=37\n"),
    )
    issues, details = [], []
    monitor.check_runtime(issues, details)
    assert issues == []
    assert "unknown_deliveries=37" in details[0]


@pytest.mark.parametrize("exit_code", [1, 124, -1])
def test_failed_or_timed_out_runtime_is_an_alert(monkeypatch, exit_code):
    monkeypatch.setattr(monitor, "run_command", lambda *a, **kw: (exit_code, ""))
    issues, details = [], []
    monitor.check_runtime(issues, details)
    assert issues == [f"runtime_check exit={exit_code}"]


def test_error_categories_deduplicate_counts_and_hide_message(monkeypatch):
    logs = "2026-11-04 10:00:00,000 ERROR tg_lottery.worker secret user message\n"
    outputs = iter([(0, "running|healthy"), (0, logs * 2)])
    monkeypatch.setattr(monitor, "run_command", lambda *a, **kw: next(outputs))
    issues, details = [], []
    monitor.check_container("worker", issues, details)
    assert issues == ["worker ERROR logger=tg_lottery.worker"]
    assert "occurrences=2" in details[-1]
    assert "secret" not in "\n".join([*issues, *details])


def test_unhealthy_container_and_log_read_failure_alert(monkeypatch):
    outputs = iter([(0, "exited|none"), (1, "permission denied")])
    monkeypatch.setattr(monitor, "run_command", lambda *a, **kw: next(outputs))
    issues, details = [], []
    monitor.check_container("worker", issues, details)
    assert len(issues) == 2
    assert "unhealthy" in issues[0]
    assert "log scan failed" in issues[1]


@pytest.mark.parametrize("code", [0, 9499])
def test_feishu_checks_business_code(monkeypatch, code):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return json.dumps({"code": code}).encode()

    def urlopen(request, *, timeout):
        requests.append(json.loads(request.data))
        assert timeout == 10
        return Response()

    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.test/hook")
    monkeypatch.setattr(monitor.urllib.request, "urlopen", urlopen)
    if code:
        with pytest.raises(RuntimeError, match="rejected"):
            monitor.send_feishu("test")
    else:
        monitor.send_feishu("test")
    assert requests == [{"msg_type": "text", "content": {"text": "test"}}]


def test_collection_failure_is_not_silent_success(notifications, monkeypatch):
    def fail():
        raise OSError("private detail")

    monkeypatch.setattr(monitor, "collect_report", fail)
    monkeypatch.setattr(monitor.os, "umask", lambda _value: None)
    assert monitor.main() == 1
    assert "collection failed error_type=OSError" in notifications[0]
    assert "private detail" not in notifications[0]


def test_subprocess_timeout_becomes_explicit_failure(monkeypatch):
    def fail(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("docker", 15)

    monkeypatch.setattr(monitor.subprocess, "run", fail)
    assert monitor.run_command(["docker"]) == (-1, "TimeoutExpired")
