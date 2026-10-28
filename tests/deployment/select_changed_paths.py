from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path


def select_groups(paths: Iterable[str]) -> dict[str, bool]:
    changed = tuple(
        path.replace("\\", "/").removeprefix("./") for path in paths if path
    )
    test_changes = any(path.startswith("tests/deployment/") for path in changed)
    workflows = test_changes or any(path.startswith(".github/") for path in changed)
    foundation = test_changes or any(
        path.startswith(("foundation/", "services/foundry-iq-docs/"))
        for path in changed
    )
    agent = test_changes or any(
        path.startswith(("agent/", "src/fibey/"))
        or path in {".dockerignore", "Dockerfile.agent", "pyproject.toml", "uv.lock"}
        for path in changed
    )
    return {
        "workflows": workflows,
        "foundation": foundation,
        "agent": agent,
        "configuration": workflows or foundation or agent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Select PR validation groups.")
    parser.add_argument("--file", required=True, help="NUL-delimited git diff path list")
    args = parser.parse_args()
    paths = Path(args.file).read_bytes().split(b"\0")
    decoded_paths = [path.decode("utf-8", errors="surrogateescape") for path in paths]
    for name, selected in select_groups(decoded_paths).items():
        print(f"{name}={str(selected).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
