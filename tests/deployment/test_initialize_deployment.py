from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/scripts/initialize-deployment.sh"


class InitializeDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / "parameters.json"
        self.foundation = json.loads(
            (ROOT / "foundation/parameters.json").read_text(encoding="utf-8")
        )
        self.foundation.update({
            "AZURE_PRINCIPAL_ID": "11111111-2222-3333-4444-555555555555",
            "INVENTORY_MCP_URL": "https://inventory.example/mcp?api-version=v1",
            "WORK_ORDERS_API_URL": "https://orders.example/api",
            "AZD_RESOURCE_TOKEN_SALT": "$(not-executed)=$HOME",
        })
        self.agent = {
            "AZURE_ENV_NAME": "independent-agent",
            "AZURE_LOCATION": "westus",
            "AZURE_RESOURCE_GROUP": "rg-agent",
            "AZURE_AI_ACCOUNT_ID": (
                "/subscriptions/sub-123/resourceGroups/rg-agent/providers/"
                "Microsoft.CognitiveServices/accounts/other-account"
            ),
            "AZURE_AI_PROJECT_ID": (
                "/subscriptions/sub-123/resourceGroups/rg-agent/providers/"
                "Microsoft.CognitiveServices/accounts/other-account/projects/other-project"
            ),
            "AZURE_AI_FOUNDRY_PROJECT_ID": (
                "/subscriptions/sub-123/resourceGroups/rg-agent/providers/"
                "Microsoft.CognitiveServices/accounts/other-account/projects/other-project"
            ),
            "AZURE_AI_ACCOUNT_NAME": "other-account",
            "AZURE_AI_PROJECT_NAME": "other-project",
            "AZURE_AI_PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/other-project"
            ),
            "FOUNDRY_PROJECT_ENDPOINT": (
                "https://other-account.services.ai.azure.com/api/projects/other-project"
            ),
            "AZURE_AI_PROJECT_ACR_CONNECTION_NAME": "other-registry",
            "AZURE_CONTAINER_REGISTRY_ENDPOINT": "other.azurecr.io",
            "FOUNDRY_MODEL": "other-model",
            "TOOLBOX_MCP_URL": "https://tools.example/toolboxes/other-toolbox/mcp",
        }
        self.bin_directory = self.root / "bin"
        self.bin_directory.mkdir()
        self.log = self.root / "commands.jsonl"
        self.summary = self.root / "summary.md"
        self.export = self.root / "github-env"
        self._write_executable(
            "azd",
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "with Path(os.environ['COMMAND_LOG']).open('a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if os.environ.get('FAIL_OPERATION') == sys.argv[2]:\n"
            "    raise SystemExit(7)\n",
        )
        self._write_executable(
            "az", "#!/usr/bin/env bash\necho 'Unexpected Azure discovery' >&2\nexit 99\n",
        )

    def _write_executable(self, name: str, source: str) -> None:
        path = self.bin_directory / name
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _environment(self, operation: str, project: str) -> dict[str, str]:
        return {
            "PATH": f"{self.bin_directory}{os.pathsep}{os.environ['PATH']}",
            "GITHUB_WORKSPACE": str(ROOT),
            "GITHUB_ENV": str(self.export),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "PARAMETER_FILE": str(self.file),
            "OPERATION": operation,
            "PROJECT_DIRECTORY": str(self.root / project),
            "AZURE_SUBSCRIPTION_ID": "sub-123",
            "AZURE_TENANT_ID": "tenant-123",
            "COMMAND_LOG": str(self.log),
        }

    def _run(self, operation: str, project: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=self._environment(operation, project),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def _commands(self) -> list[list[str]]:
        return [
            json.loads(line)
            for line in self.log.read_text(encoding="utf-8").splitlines()
        ]

    def test_foundation_parameters_are_directly_loaded_without_resource_discovery(self) -> None:
        self.file.write_text(json.dumps(self.foundation), encoding="utf-8")
        result = self._run("foundation-deploy", "foundation")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self._commands()
        self.assertEqual(commands[0][:3], ["env", "new", "fibey-jf-1026"])
        self.assertEqual(commands[1][:2], ["env", "set"])
        settings = dict(item.split("=", 1) for item in commands[1][2:-4])
        self.assertEqual(settings["AZURE_PRINCIPAL_ID"], self.foundation["AZURE_PRINCIPAL_ID"])
        self.assertEqual(settings["INVENTORY_MCP_URL"], self.foundation["INVENTORY_MCP_URL"])
        self.assertEqual(settings["FOUNDRY_MODEL_CAPACITY"], "100")
        self.assertEqual(settings["ENABLE_HOSTED_AGENTS"], "true")
        self.assertEqual(settings["AI_PROJECT_CONNECTIONS"], "[]")
        self.assertEqual(settings["AZURE_TENANT_ID"], "tenant-123")
        self.assertEqual(self.export.read_text(encoding="utf-8"), "AZURE_ENV_NAME=fibey-jf-1026\n")
        self.assertNotIn("not-executed", result.stdout + result.stderr + self.summary.read_text())

    def test_agent_file_supplies_all_target_settings_without_foundation_import(self) -> None:
        self.file.write_text(json.dumps(self.agent), encoding="utf-8")
        result = self._run("agent", "agent")
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self._commands()
        self.assertEqual(commands[0][:3], ["env", "new", "independent-agent"])
        settings = dict(item.split("=", 1) for item in commands[1][2:-4])
        for key, value in self.agent.items():
            self.assertEqual(settings[key], value)
        self.assertEqual(settings["AZURE_TENANT_ID"], "tenant-123")
        self.assertEqual(self.export.read_text(encoding="utf-8"), "AZURE_ENV_NAME=independent-agent\n")

    def test_validation_modes_check_json_without_azure_or_azd(self) -> None:
        for lifecycle, filename in (
            ("foundation-validate", ROOT / "foundation/parameters.json"),
            ("agent-validate", ROOT / "agent/parameters.json"),
        ):
            with self.subTest(lifecycle=lifecycle):
                environment = self._environment(lifecycle, "unused")
                environment.pop("AZURE_SUBSCRIPTION_ID")
                environment.pop("AZURE_TENANT_ID")
                environment.pop("COMMAND_LOG")
                environment["PARAMETER_FILE"] = str(filename)
                result = subprocess.run(
                    ["bash", str(SCRIPT)], env=environment,
                    capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())

    def test_invalid_inputs_fail_before_azd_or_github_exports(self) -> None:
        cases = [
            ("foundation-preview", self.foundation | {"AZURE_PRINCIPAL_ID": ""}),
            ("foundation-deploy", self.foundation | {"WORK_ORDERS_API_URL": ""}),
            ("agent", self.agent | {"TOOLBOX_MCP_URL": ""}),
            ("agent", self.agent | {"FOUNDRY_PROJECT_ENDPOINT": "https://different.example"}),
            ("agent", self.agent | {"AZURE_AI_PROJECT_ID": "/subscriptions/other-sub/project"}),
            ("foundation-preview", self.foundation | {"BAD=KEY": "value"}),
            ("foundation-preview", self.foundation | {"AZURE_LOCATION": "bad\nvalue"}),
        ]
        for operation, parameters in cases:
            with self.subTest(operation=operation, parameters=parameters):
                self.file.write_text(json.dumps(parameters), encoding="utf-8")
                self.log.unlink(missing_ok=True)
                self.export.unlink(missing_ok=True)
                self.summary.unlink(missing_ok=True)
                result = self._run(operation, "project")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
                self.assertFalse(self.export.exists())
                self.assertFalse(self.summary.exists())
        for invalid in ("{", "[]"):
            self.file.write_text(invalid, encoding="utf-8")
            self.assertNotEqual(self._run("foundation-preview", "project").returncode, 0)

    def test_azd_failures_propagate_without_success_summary(self) -> None:
        self.file.write_text(json.dumps(self.foundation), encoding="utf-8")
        for operation, expected_commands in (("new", 1), ("set", 2)):
            self.log.unlink(missing_ok=True)
            self.export.unlink(missing_ok=True)
            self.summary.unlink(missing_ok=True)
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                env={**self._environment("foundation-deploy", "foundation"), "FAIL_OPERATION": operation},
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(len(self._commands()), expected_commands)
            self.assertFalse(self.export.exists())
            self.assertFalse(self.summary.exists())

    def test_parameter_files_are_isolated_and_workflows_use_their_own_files(self) -> None:
        foundation = json.loads((ROOT / "foundation/parameters.json").read_text())
        agent = json.loads((ROOT / "agent/parameters.json").read_text())
        self.assertTrue({"AZURE_ENV_NAME", "AZURE_LOCATION", "FOUNDRY_MODEL"} <= set(agent))
        self.assertNotIn("AZURE_PRINCIPAL_ID", agent)
        self.assertNotIn("INVENTORY_MCP_URL", agent)
        self.assertNotIn("WORK_ORDERS_API_URL", agent)
        self.assertNotIn("AZURE_AI_PROJECT_ID", foundation)
        foundation_workflow = yaml.load(
            (ROOT / ".github/workflows/deploy-foundation.yml").read_text(), Loader=yaml.BaseLoader,
        )
        agent_workflow = yaml.load(
            (ROOT / ".github/workflows/deploy-agent.yml").read_text(), Loader=yaml.BaseLoader,
        )
        self.assertEqual(foundation_workflow["concurrency"], agent_workflow["concurrency"])
        for lifecycle, workflow in (("foundation", foundation_workflow), ("agent", agent_workflow)):
            self.assertEqual(
                workflow["on"]["workflow_dispatch"]["inputs"]["parameter_file"]["default"],
                f"{lifecycle}/parameters.json",
            )
            for job in workflow["jobs"].values():
                steps = job["steps"]
                self.assertIn(
                    "./.github/actions/initialize-deployment",
                    [step.get("uses", "") for step in steps],
                )
                self.assertNotIn("vars.", json.dumps(steps))
                if lifecycle == "agent":
                    self.assertNotIn("foundation/", json.dumps(steps))
        commands = [
            step["with"].get("command", "deploy")
            for step in foundation_workflow["jobs"]["provision-and-toolbox"]["steps"]
            if step.get("uses") == "./.github/actions/run-azd"
        ]
        self.assertEqual(commands, ["provision", "deploy"])


if __name__ == "__main__":
    unittest.main()
