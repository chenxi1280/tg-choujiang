from __future__ import annotations

import argparse
import subprocess
import sys

TEST_TIMEOUT_SECONDS = 60


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one deterministic pytest node shard")
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    return parser.parse_args()


def _collected_node_ids() -> tuple[str, ...]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(line for line in result.stdout.splitlines() if "::" in line)


def main() -> int:
    args = _arguments()
    if args.count < 1 or not 0 <= args.index < args.count:
        raise ValueError("shard index must satisfy 0 <= index < count")
    node_ids = _collected_node_ids()
    selected = node_ids[args.index :: args.count]
    if not selected:
        raise RuntimeError(f"pytest shard {args.index}/{args.count} is empty")
    print(f"pytest shard {args.index + 1}/{args.count}: {len(selected)} nodes", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *selected],
        timeout=TEST_TIMEOUT_SECONDS,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
