from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    with Path(sys.argv[1]).open("rb") as source:
        project = tomllib.load(source)
    requirements = [
        *project["build-system"]["requires"],
        *project["project"]["dependencies"],
        *project["project"]["optional-dependencies"]["dev"],
    ]
    subprocess.run([sys.executable, "-m", "pip", "install", *requirements], check=True)


if __name__ == "__main__":
    main()
