"""Host-side, read-only service monitor; Python 3.6+ standard library only."""

import collections
import datetime
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE_DIR = Path(os.environ.get("BASE_DIR", "/data/tg-lottery"))
STATE_FILE = BASE_DIR / "shared/service-health.json"
ERROR_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \S+ (ERROR|CRITICAL|FATAL|PANIC) ([A-Za-z0-9_.]{1,100}) "
)
HEALTH_LINE = re.compile(
    r"(?:STATUS=|CHECK_STATUS=|UNKNOWN_ACKNOWLEDGED |UNKNOWN_UNREVIEWED |"
    r"UNKNOWN_REVIEW_INVALIDATED |rejected_ui_responses=|app |worker )"
    r"[A-Za-z0-9_= .:+-]+"
)


def run_command(command, *, timeout=15):
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,  # noqa: UP021 - Host Python is 3.6.
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, type(exc).__name__


def check_runtime(issues, details):
    code, output = run_command(
        [
            "timeout",
            "--kill-after=5s",
            "90s",
            "bash",
            str(BASE_DIR / "current/deploy/check-services.sh"),
        ],
        timeout=100,
    )
    lines = [line for line in output.splitlines() if HEALTH_LINE.fullmatch(line)]
    for line in lines:
        print(line, flush=True)
    details.extend(line for line in lines if not line.startswith("UNKNOWN_ACKNOWLEDGED "))
    if code:
        issues.append(f"runtime_check exit={code}")
        issues.extend(
            line
            for line in lines
            if line.startswith(("STATUS=", "UNKNOWN_UNREVIEWED ", "UNKNOWN_REVIEW_INVALIDATED "))
        )


def monitored_containers(issues):
    containers = ["tg-lottery-app", "tg-lottery-worker"]
    if (BASE_DIR / "shared/lottery-integration.env").is_file():
        containers.append("tg-lottery-integration")
    code, output = run_command(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=tg-lottery",
            "--filter",
            "label=com.docker.compose.service=ops-bot",
            "--format",
            "{{.Names}}",
        ]
    )
    if code:
        issues.append(f"ops-bot discovery failed exit={code}")
    else:
        containers.extend(name for name in output.splitlines() if re.fullmatch(r"[\w-]+", name))
    return containers


def check_container(name, issues, details):
    code, output = run_command(
        [
            "docker",
            "inspect",
            name,
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
        ]
    )
    state = output.strip()
    if code or state not in ("running|healthy", "running|none"):
        issues.append(f"{name} container unavailable or unhealthy")
    else:
        details.append(f"{name} {state}")
    code, output = run_command(["docker", "logs", "--since", "30m", name])
    if code:
        issues.append(f"{name} log scan failed exit={code}")
        return
    errors = collections.Counter()
    for line in output.splitlines():
        match = ERROR_LINE.match(line)
        if match:
            errors["{} logger={}".format(*match.groups())] += 1
        elif line.startswith("Traceback (most recent call last):"):
            errors["Python traceback"] += 1
    for category, count in sorted(errors.items()):
        issue = f"{name} {category}"
        issues.append(issue)
        details.append(f"{issue} occurrences={count}")


def check_resources(issues, details):
    memory = re.search(r"^MemAvailable:\s+(\d+)", Path("/proc/meminfo").read_text(), re.M)
    available_mb = int(memory.group(1)) // 1024
    usage = shutil.disk_usage(str(BASE_DIR))
    disk_percent = usage.used * 100 // usage.total
    details.extend([f"available_memory_mb={available_mb}", f"disk_used_percent={disk_percent}"])
    if available_mb < 128:
        issues.append("available memory below 128 MiB")
    if disk_percent >= 90:
        issues.append("disk usage at or above 90%")


def collect_report():
    issues, details = [], []
    check_runtime(issues, details)
    for name in monitored_containers(issues):
        check_container(name, issues, details)
    check_resources(issues, details)
    return {"issues": sorted(set(issues)), "details": details}


def send_feishu(message):
    payload = json.dumps({"msg_type": "text", "content": {"text": message}}).encode("utf-8")
    request = urllib.request.Request(
        os.environ["FEISHU_WEBHOOK_URL"],
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code") != 0:
        code = result.get("code")
        print(
            "FEISHU_STATUS=REJECTED code={}".format(
                code if isinstance(code, int) else "missing_or_invalid"
            ),
            flush=True,
        )
        raise RuntimeError("Feishu rejected notification")
    print("FEISHU_STATUS=SENT code=0", flush=True)


def notification_title(report, previous, now):
    issues = set(report["issues"])
    previous_issues = set(previous.get("issues", []))
    if issues:
        if issues - previous_issues:
            return "线上告警"
        if previous_issues - issues:
            return "告警范围缩小"
        return None
    if previous_issues:
        return "告警恢复"
    if not previous:
        return "监控已启用"
    if now.hour == 9 and previous.get("last_daily_date") != now.date().isoformat():
        return "每日巡检正常"
    return None


def process_report(report, *, now):
    previous = json.loads(STATE_FILE.read_text()) if STATE_FILE.is_file() else {}
    title = notification_title(report, previous, now)
    if title:
        message = "【tg-choujiang {}】\n时间: {} 北京时间\n主机: {}\n\n{}\n\n{}".format(
            title,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            socket.gethostname(),
            "\n".join(report["issues"]) or "当前巡检通过。",
            "\n".join(report["details"]),
        )
        message += "\n\n已知悉的历史 UNKNOWN 仍未确认。巡检通过不表示 Telegram 业务已成功。"
        send_feishu(message[:5000])
    daily_date = previous.get("last_daily_date", "")
    if title and not report["issues"] and now.hour == 9:
        daily_date = now.date().isoformat()
    state = {"issues": report["issues"], "last_daily_date": daily_date}
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False))
    temporary.replace(STATE_FILE)
    print(
        "MONITOR_STATUS={} notification={}".format(
            "ALERT" if report["issues"] else "PASSED", title or "DEDUPLICATED"
        ),
        flush=True,
    )
    return 1 if report["issues"] else 0


def main():
    os.umask(0o077)
    try:
        report = collect_report()
    except Exception as exc:
        report = {
            "issues": [f"monitor collection failed error_type={type(exc).__name__}"],
            "details": [],
        }
    try:
        print(json.dumps(report, ensure_ascii=False), flush=True)
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
        return process_report(report, now=now)
    except Exception as exc:
        print(f"MONITOR_STATUS=FAILED error_type={type(exc).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
