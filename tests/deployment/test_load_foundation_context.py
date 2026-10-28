from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from test_resolve_deployment_context import arm_deployment, resolve


ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/load-foundation-context/action.yml"
SCRIPT = ROOT / ".github/scripts/load-foundation-context.sh"
RESOLVER = ROOT / "foundation/scripts/resolve-deployment-context.py"


class LoadFoundationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        self.log = self.root / "commands.jsonl"
        self.summary = self.root / "summary.md"
        self.deployment = arm_deployment()
        self.fixture = self.root / "deployment.json"
        self.inventory_url = (
            "https://inventory.example/mcp?q=two words&eq=a=b"
            "&quote=\"'&slash=\\&literal=$HOME&cmd=$(printf literal)"
        )
        self.work_orders_url = "https://orders.example/api?key=a=b&path=one two"
        action = yaml.load(ACTION.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        step = next(
            step for step in action["runs"]["steps"]
            if step["name"] == "Resolve selected ARM deployment"
        )
        self.assertIn("load-foundation-context.sh", step["run"])
        mock_source = """
import json
import os
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
arguments = sys.argv[1:]
with Path(os.environ["COMMAND_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": tool, "arguments": arguments}) + chr(10))
if tool == "az":
    if arguments[:3] not in (
        ["deployment", "sub", "show"],
        ["deployment", "sub", "list"],
    ):
        print(f"Unexpected az command: {arguments}", file=sys.stderr)
        raise SystemExit(2)
    print(Path(os.environ["DEPLOYMENT_FIXTURE"]).read_text(encoding="utf-8"))
elif arguments[:2] in (["env", "new"], ["env", "set"]):
    if os.environ.get("FAIL_AZD_OPERATION") == arguments[1]:
        print("Mock azd failure", file=sys.stderr)
        raise SystemExit(7)
else:
    print(f"Unexpected azd command: {arguments}", file=sys.stderr)
    raise SystemExit(2)
"""
        for tool in ("az", "azd"):
            self._write_executable(tool, mock_source)

    def _write_executable(self, name: str, source: str) -> None:
        path = self.bin_directory / name
        path.write_text(f"#!/usr/bin/env python3\n{source}", encoding="utf-8")
        path.chmod(0o755)

    def _run(
        self,
        fail_operation: str = "",
        deployment_name: str = "selected-deployment",
        deployment_data: object | None = None,
    ) -> subprocess.CompletedProcess[str]:
        fixture = self.deployment if deployment_data is None else deployment_data
        self.fixture.write_text(json.dumps(fixture), encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.bin_directory}{os.pathsep}{environment['PATH']}",
                "COMMAND_LOG": str(self.log),
                "DEPLOYMENT_FIXTURE": str(self.fixture),
                "FAIL_AZD_OPERATION": fail_operation,
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "PROJECT_DIRECTORY": "foundation",
                "RESOLVER_PATH": str(RESOLVER),
                "DEPLOYMENT_NAME": deployment_name,
                "AZURE_ENVIRONMENT_NAME": "fibey-jf-1026",
                "AZURE_SUBSCRIPTION_ID": "sub-123",
                "AZURE_TENANT_ID": "tenant-123",
                "AZURE_RESOURCE_GROUP": "rg-fibey-jf-1026",
                "AZURE_AI_ACCOUNT_NAME": "ai-account-sidzt6mr5x5qu",
                "AZURE_AI_PROJECT_NAME": "fibey-project",
                "AZURE_PRIMARY_LOCATION": "eastus2",
                "AZURE_SEARCH_LOCATION": "centralus",
                "FOUNDRY_MODEL_VERSION": "2025-04-14",
                "FOUNDRY_MODEL_CAPACITY": "100",
                "AZURE_PRINCIPAL_ID": "principal-123",
                "AZURE_PRINCIPAL_TYPE": "ServicePrincipal",
                "INVENTORY_MCP_URL": self.inventory_url,
                "WORK_ORDERS_API_URL": self.work_orders_url,
                "KB_NAME": "fibey-field-ops-kb",
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def _azd_commands(self) -> list[list[str]]:
        return [
            command["arguments"]
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if (command := json.loads(line))["tool"] == "azd"
        ]

    def _az_commands(self) -> list[list[str]]:
        return [
            command["arguments"]
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if (command := json.loads(line))["tool"] == "az"
        ]

    def test_creates_environment_and_writes_complete_context_once(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._az_commands(),
            [[
                "deployment", "sub", "show", "--name", "selected-deployment",
                "--subscription", "sub-123", "--output", "json",
            ]],
        )
        commands = self._azd_commands()
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            commands[0],
            [
                "env", "new", "fibey-jf-1026", "--no-prompt",
                "--subscription", "sub-123", "--location", "eastus2",
                "--cwd", "foundation",
            ],
        )
        self.assertEqual(commands[1][:2], ["env", "set"])
        self.assertEqual(
            commands[1][-4:],
            ["--environment", "fibey-jf-1026", "--cwd", "foundation"],
        )
        assignments = commands[1][2:-4]
        actual = dict(assignment.split("=", 1) for assignment in assignments)
        self.assertEqual(len(assignments), len(actual))
        self.assertEqual(
            actual,
            resolve(
                self.deployment,
                inventory_mcp_url=self.inventory_url,
                work_orders_api_url=self.work_orders_url,
                principal_id="principal-123",
                principal_type="ServicePrincipal",
            ),
        )
        self.assertNotIn(self.inventory_url, result.stdout + result.stderr)
        self.assertNotIn(self.inventory_url, self.summary.read_text(encoding="utf-8"))

    def test_discovers_latest_matching_deployment_when_name_is_omitted(self) -> None:
        older = arm_deployment(
            name="older",
            timestamp="2026-10-27T10:00:00Z",
        )
        latest = arm_deployment(
            name="latest",
            timestamp="2026-10-28T10:00:00Z",
        )
        unrelated = arm_deployment(
            name="unrelated",
            resource_group="rg-other",
            timestamp="2026-10-29T10:00:00Z",
        )
        result = self._run(
            deployment_name="",
            deployment_data=[older, unrelated, latest],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._az_commands(),
            [[
                "deployment", "sub", "list", "--subscription", "sub-123",
                "--output", "json",
            ]],
        )
        commands = self._azd_commands()
        assignments = commands[1][2:-4]
        actual = dict(assignment.split("=", 1) for assignment in assignments)
        self.assertEqual(
            actual,
            resolve(
                latest,
                deployment_name="latest",
                inventory_mcp_url=self.inventory_url,
                work_orders_api_url=self.work_orders_url,
                principal_id="principal-123",
                principal_type="ServicePrincipal",
            ),
        )

    def test_discovery_fails_clearly_when_no_prior_deployment_matches(self) -> None:
        result = self._run(
            deployment_name="",
            deployment_data=[
                arm_deployment(resource_group="rg-other"),
            ],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No successful subscription-scope ARM deployment matched", result.stderr)
        self.assertEqual(self._azd_commands(), [])
        self.assertFalse(self.summary.exists())

    def test_stops_if_environment_creation_fails(self) -> None:
        result = self._run(fail_operation="new")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(self._azd_commands()), 1)
        self.assertFalse(self.summary.exists())

    def test_propagates_bulk_write_failure_without_success_summary(self) -> None:
        result = self._run(fail_operation="set")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(self._azd_commands()), 2)
        self.assertFalse(self.summary.exists())

    def test_rejects_invalid_context_before_initializing_environment(self) -> None:
        self.deployment["properties"]["provisioningState"] = "Failed"
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has not succeeded", result.stderr)
        self.assertEqual(self._azd_commands(), [])
        self.assertFalse(self.summary.exists())

    def test_propagates_conversion_failure_before_initializing_environment(self) -> None:
        self._write_executable(
            "jq",
            "import sys\nprint('Mock conversion failure', file=sys.stderr)\nraise SystemExit(8)\n",
        )
        result = self._run()
        self.assertEqual(result.returncode, 8)
        self.assertEqual(self._azd_commands(), [])
        self.assertFalse(self.summary.exists())


if __name__ == "__main__":
    unittest.main()
