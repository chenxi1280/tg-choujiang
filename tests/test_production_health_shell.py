import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run_check(tmp_path, *, delivery_exit, app_exit=0, app_health="healthy"):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = info ]; then exit 0; fi\n'
        'if [ "$1" = inspect ] && [ "$2" = tg-lottery-app ]; then\n'
        '  echo "running|${FAKE_APP_HEALTH:-healthy}"; exit 0\n'
        "fi\n"
        'if [ "$1" = inspect ]; then echo "running|healthy"; exit 0; fi\n'
        'if [ "$1" = exec ] && [ "$4" = "-m" ] && '
        '[ "$5" = "tg_lottery.ops.delivery_health" ]; then\n'
        '  printf "%s\\n" "$FAKE_REPORT"; exit "$FAKE_EXIT"\n'
        "fi\n"
        'if [ "$1" = exec ] && [ "$2" = tg-lottery-app ] && '
        '[ "$5" = tg_lottery.healthcheck ]; then exit "${FAKE_APP_EXIT:-0}"; fi\n'
        'if [ "$1" = exec ]; then exit 0; fi\n'
        "exit 3\n"
    )
    docker.chmod(0o755)
    env_file = tmp_path / "runtime.env"
    env_file.write_text(
        "BOT_TOKEN=test\nDATABASE_URL=test\nAPP_ENCRYPTION_KEY=test\nTG_LOTTERY_APP_IMAGE=test\n"
    )
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ENV_FILE": str(env_file),
        "IMAGE_ENV_FILE": str(tmp_path / "absent-image.env"),
        "FAKE_REPORT": "STATUS=DEGRADED_ACKNOWLEDGED unknown_deliveries=37",
        "FAKE_EXIT": str(delivery_exit),
        "FAKE_APP_EXIT": str(app_exit),
        "FAKE_APP_HEALTH": app_health,
    }
    return subprocess.run(
        ["bash", str(ROOT / "deploy/check-services.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_delivery_health_result_controls_check_script(tmp_path, exit_code):
    result = _run_check(tmp_path, delivery_exit=exit_code)
    assert result.returncode == exit_code
    assert "unknown_deliveries=37" in result.stdout


def test_application_failure_still_prints_delivery_warning(tmp_path):
    result = _run_check(tmp_path, delivery_exit=0, app_exit=4)
    assert result.returncode == 4
    assert "unknown_deliveries=37" in result.stdout


def test_unhealthy_container_still_fails_even_if_process_probe_passes(tmp_path):
    result = _run_check(tmp_path, delivery_exit=0, app_health="unhealthy")
    assert result.returncode == 1
    assert "app running unhealthy" in result.stdout
    assert "unknown_deliveries=37" in result.stdout
    assert "CHECK_STATUS=FAILED" in result.stdout
