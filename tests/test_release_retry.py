import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _install_fakes(tmp_path):
    programs = {
        "git": """#!/usr/bin/env bash
case "$1" in
  branch) echo master ;;
  rev-parse) if [[ "$2" == --short ]]; then echo 1234567; else printf '%040d\\n' 1; fi ;;
  archive) exit 0 ;;
  *) exit 81 ;;
esac
""",
        "ssh": """#!/usr/bin/env bash
printf 'ssh\\n' >> "$FAKE_CALLS"
if [[ "$FAKE_SCENARIO" == ssh_failure ]]; then exit 73; fi
if [[ "$FAKE_SCENARIO" == ssh_transient ]] && [[ $(grep -c '^ssh$' "$FAKE_CALLS") == 1 ]]; then
  exit 73
fi
""",
        "scp": """#!/usr/bin/env bash
printf 'scp\\n' >> "$FAKE_CALLS"
if [[ "$FAKE_SCENARIO" == scp_failure ]]; then exit 74; fi
exit 0
""",
    }
    for name, content in programs.items():
        program = tmp_path / name
        program.write_text(content)
        program.chmod(0o755)


def _run_release(tmp_path, scenario):
    _install_fakes(tmp_path)
    calls = tmp_path / "calls"
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_CALLS": str(calls),
        "FAKE_SCENARIO": scenario,
        "RELEASE_SSH_ATTEMPTS": "3",
        "RELEASE_SSH_RETRY_DELAY": "0",
    }
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy/release.sh"),
            "--host",
            "fake-host",
            "--base-dir",
            "/tmp/simulated-tg-lottery",
            "--branch-list",
            "master",
            "--allow-dirty",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return result, calls.read_text().splitlines()


@pytest.mark.parametrize(
    "spec",
    [
        ("ssh_failure", 73, ["ssh", "ssh", "ssh"]),
        ("scp_failure", 74, ["ssh", "scp", "scp", "scp"]),
        ("ssh_transient", 0, ["ssh", "ssh", "scp", "scp", "ssh"]),
    ],
)
def test_release_retry_preserves_command_failure_or_recovers(tmp_path, spec):
    scenario, expected_exit, calls = spec
    result, observed = _run_release(tmp_path, scenario)
    assert result.returncode == expected_exit, result.stderr
    assert observed == calls
    assert ("deployed" in result.stdout) is (expected_exit == 0)
