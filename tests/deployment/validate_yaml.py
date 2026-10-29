from __future__ import annotations

import sys
import subprocess
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
FILES = tuple(
    sorted(
        path.relative_to(ROOT).as_posix()
        for suffix in ("*.yml", "*.yaml")
        for path in (ROOT / ".github").rglob(suffix)
    )
) + (
    "foundation/azure.yaml",
    "agent/azure.yaml",
)
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}


class DuplicateKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(
    loader: DuplicateKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    keys = [loader.construct_object(key_node, deep=False) for key_node, _ in node.value]
    duplicates = [key for index, key in enumerate(keys) if key in keys[:index]]
    if duplicates:
        raise yaml.YAMLError(f"Duplicate key(s) {duplicates!r} at {node.start_mark}")
    return loader.construct_mapping(node, deep=deep)


DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _validate_local_references(value: Any, document: dict[str, Any]) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            target: Any = document
            for segment in reference[2:].split("/"):
                key = segment.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or key not in target:
                    raise ValueError(f"Unresolved OpenAPI reference {reference!r}")
                target = target[key]
        for child in value.values():
            _validate_local_references(child, document)
    elif isinstance(value, list):
        for child in value:
            _validate_local_references(child, document)


def _validate_toolbox_openapi(document: dict[str, Any]) -> None:
    services = document.get("services")
    toolbox = services.get("fibey-toolbox") if isinstance(services, dict) else None
    tools = toolbox.get("tools") if isinstance(toolbox, dict) else None
    openapi_tools = [
        tool for tool in tools or []
        if isinstance(tool, dict) and tool.get("type") == "openapi"
    ]
    if len(openapi_tools) != 1:
        raise ValueError("Expected exactly one OpenAPI tool in the Fibey toolbox.")
    openapi = openapi_tools[0].get("openapi")
    spec = openapi.get("spec") if isinstance(openapi, dict) else None
    if not isinstance(spec, dict) or spec.get("openapi") not in {"3.0.0", "3.1.0"}:
        raise ValueError("The Fibey work-orders tool has no supported OpenAPI specification.")
    info = spec.get("info")
    paths = spec.get("paths")
    if (
        not isinstance(info, dict)
        or not isinstance(info.get("title"), str)
        or not isinstance(info.get("version"), str)
        or not isinstance(paths, dict)
        or not paths
    ):
        raise ValueError("The Fibey work-orders OpenAPI info or paths are incomplete.")

    operation_ids: set[str] = set()
    for path, path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(path_item, dict):
            raise ValueError(f"Invalid OpenAPI path entry {path!r}.")
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError(f"Invalid OpenAPI operation for {method.upper()} {path}.")
            operation_id = operation.get("operationId")
            responses = operation.get("responses")
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError(f"OpenAPI operation {method.upper()} {path} has no operationId.")
            if operation_id in operation_ids:
                raise ValueError(f"Duplicate OpenAPI operationId {operation_id!r}.")
            if not isinstance(responses, dict) or not responses:
                raise ValueError(f"OpenAPI operation {operation_id!r} has no responses.")
            operation_ids.add(operation_id)
    if not operation_ids:
        raise ValueError("The Fibey work-orders OpenAPI specification has no operations.")
    _validate_local_references(spec, spec)


def _validate_agent_build_context(document: dict[str, Any]) -> None:
    services = document.get("services")
    service = services.get("fibey-agent") if isinstance(services, dict) else None
    docker = service.get("docker") if isinstance(service, dict) else None
    if (
        not isinstance(service, dict)
        or service.get("project") != ".."
        or not isinstance(docker, dict)
        or docker.get("path") != "./Dockerfile.agent"
        or docker.get("remoteBuild") is not True
    ):
        raise ValueError("The hosted agent must use the root project and remote Dockerfile.agent build.")
    project_root = (ROOT / "agent" / service["project"]).resolve()
    if project_root != ROOT.resolve():
        raise ValueError("The hosted-agent project path does not resolve to the repository root.")
    required_inputs = (
        "Dockerfile.agent",
        "pyproject.toml",
        "uv.lock",
        "src/fibey/agent/hosted.py",
    )
    missing = [relative for relative in required_inputs if not (project_root / relative).is_file()]
    if missing:
        raise ValueError(f"The hosted-agent root build context is missing: {', '.join(missing)}.")


def main() -> int:
    for relative_path in FILES:
        path = ROOT / relative_path
        try:
            document = yaml.load(path.read_text(encoding="utf-8"), Loader=DuplicateKeyLoader)
        except (OSError, yaml.YAMLError) as error:
            print(f"ERR {relative_path}: {error}", file=sys.stderr)
            return 1
        if not isinstance(document, dict):
            print(f"ERR {relative_path}: expected a YAML mapping", file=sys.stderr)
            return 1
        if relative_path == "foundation/azure.yaml":
            try:
                _validate_toolbox_openapi(document)
            except ValueError as error:
                print(f"ERR {relative_path}: {error}", file=sys.stderr)
                return 1
            print("OK  foundation/azure.yaml toolbox OpenAPI")
        if relative_path == "agent/azure.yaml":
            try:
                _validate_agent_build_context(document)
            except ValueError as error:
                print(f"ERR {relative_path}: {error}", file=sys.stderr)
                return 1
            print("OK  agent/azure.yaml root build context")
        print(f"OK  {relative_path}")
        if relative_path.endswith("action.yml"):
            jobs = document.get("runs", {}).get("steps", [])
        elif relative_path.startswith(".github/workflows/"):
            jobs = [
                step
                for job in document.get("jobs", {}).values()
                for step in job.get("steps", [])
            ]
        else:
            continue
        for index, step in enumerate(jobs, start=1):
            script = step.get("run")
            if not isinstance(script, str):
                continue
            result = subprocess.run(
                ["bash", "-n"],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                print(
                    f"ERR {relative_path} run step {index}: {result.stderr.strip()}",
                    file=sys.stderr,
                )
                return 1
        print(f"OK  bash syntax {relative_path}")
    scripts = sorted((ROOT / ".github/scripts").glob("*.sh"))
    scripts.append(ROOT / "scripts/deploy-prerequisites.sh")
    for script in scripts:
        result = subprocess.run(
            ["bash", "-n", str(script)], text=True, capture_output=True, check=False
        )
        if result.returncode:
            print(f"ERR {script.name}: {result.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"OK  bash syntax {script.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
