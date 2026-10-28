#!/usr/bin/env python3
"""Promote exactly the toolbox version created by the current azd deployment."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLBOX_NAME = "fibey-toolbox"


def _baseline_path() -> Path:
    environment = os.environ.get("AZURE_ENV_NAME")
    if not environment:
        raise RuntimeError("AZURE_ENV_NAME is required to track the toolbox deployment.")

    environment_hash = hashlib.sha256(environment.encode("utf-8")).hexdigest()[:16]
    return ROOT / ".azure" / f"toolbox-version-baseline-{environment_hash}.json"


def _parse_versions(output: str) -> dict[str, str]:
    json_start = output.find("{")
    if json_start < 0:
        raise RuntimeError("azd returned no JSON while listing toolbox versions.")

    try:
        payload: Any = json.loads(output[json_start:])
    except json.JSONDecodeError as error:
        raise RuntimeError("azd returned invalid JSON while listing toolbox versions.") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
        raise RuntimeError("The toolbox version list has an unexpected JSON shape.")

    versions: dict[str, str] = {}
    for item in payload["versions"]:
        if not isinstance(item, dict):
            raise RuntimeError("The toolbox version list contains an invalid entry.")
        version_id = item.get("id")
        version = item.get("version")
        if not isinstance(version_id, str) or not isinstance(version, str):
            raise RuntimeError("A toolbox version entry is missing its id or version.")
        if version_id in versions:
            raise RuntimeError(f"The toolbox version list contains duplicate id {version_id!r}.")
        versions[version_id] = version
    return versions


def _run_azd(*arguments: str) -> str:
    result = subprocess.run(
        ["azd", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        if result.stdout:
            sys.stderr.write(result.stdout)
        raise RuntimeError(
            f"azd {' '.join(arguments)} failed with exit code {result.returncode}."
        )
    return result.stdout


def _list_versions() -> tuple[dict[str, str], str | None]:
    output = _run_azd(
        "ai",
        "toolbox",
        "versions",
        "list",
        TOOLBOX_NAME,
        "--output",
        "json",
        "--no-prompt",
    )
    versions = _parse_versions(output)
    json_start = output.find("{")
    payload: dict[str, Any] = json.loads(output[json_start:])
    default_version = payload.get("default_version")
    if default_version is not None and not isinstance(default_version, str):
        raise RuntimeError("The toolbox version list has an invalid default_version.")
    return versions, default_version


def record_baseline() -> None:
    # versions list fails for an absent toolbox in azure.ai.toolboxes beta.12.
    output = _run_azd("ai", "toolbox", "list", "--output", "json", "--no-prompt")
    json_start = output.find("{")
    if json_start < 0:
        raise RuntimeError("azd returned no JSON while listing toolboxes.")
    try:
        payload: Any = json.loads(output[json_start:])
    except json.JSONDecodeError as error:
        raise RuntimeError("azd returned invalid JSON while listing toolboxes.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("toolboxes"), list):
        raise RuntimeError("The toolbox list has an unexpected JSON shape.")
    names: set[str] = set()
    for toolbox in payload["toolboxes"]:
        if (
            not isinstance(toolbox, dict)
            or not isinstance(toolbox.get("name"), str)
            or not toolbox["name"]
            or toolbox["name"] in names
        ):
            raise RuntimeError("The toolbox list contains an invalid or duplicate entry.")
        names.add(toolbox["name"])
    versions, _ = _list_versions() if TOOLBOX_NAME in names else ({}, None)
    path = _baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps({"toolbox": TOOLBOX_NAME, "version_ids": sorted(versions)}),
        encoding="utf-8",
    )
    temporary_path.replace(path)
    print(f"Recorded {len(versions)} existing versions of {TOOLBOX_NAME}.")


def promote_deployed_version() -> None:
    path = _baseline_path()
    try:
        baseline: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(
            "No toolbox version baseline exists. Deploy with azd so the predeploy hook runs."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("The toolbox version baseline is invalid JSON.") from error

    if (
        not isinstance(baseline, dict)
        or baseline.get("toolbox") != TOOLBOX_NAME
        or not isinstance(baseline.get("version_ids"), list)
        or not all(isinstance(item, str) for item in baseline["version_ids"])
    ):
        raise RuntimeError("The toolbox version baseline has an unexpected format.")

    previous_ids = set(baseline["version_ids"])
    versions, _ = _list_versions()
    new_versions = {
        version_id: version
        for version_id, version in versions.items()
        if version_id not in previous_ids
    }
    if len(new_versions) != 1:
        raise RuntimeError(
            "Expected exactly one newly created toolbox version after deployment; "
            f"found {len(new_versions)}. No version was promoted."
        )

    version_id, version = next(iter(new_versions.items()))
    publish_output = _run_azd(
        "ai",
        "toolbox",
        "publish",
        TOOLBOX_NAME,
        version,
        "--no-prompt",
    )
    if publish_output:
        sys.stdout.write(publish_output)

    _, default_version = _list_versions()
    if default_version != version:
        raise RuntimeError(
            f"Toolbox version {version} was published but is not the default "
            f"(current default: {default_version!r})."
        )

    path.unlink(missing_ok=True)
    print(f"Promoted {TOOLBOX_NAME} version {version} ({version_id}).")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"before", "after"}:
        print(f"Usage: {Path(sys.argv[0]).name} before|after", file=sys.stderr)
        return 2
    try:
        if sys.argv[1] == "before":
            record_baseline()
        else:
            promote_deployed_version()
    except (OSError, RuntimeError) as error:
        print(f"Toolbox version promotion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
