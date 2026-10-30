from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = (
    "https://example.services.ai.azure.com/api/projects/test/"
    "toolboxes/fibey-toolbox/versions/2/mcp?api-version=v1"
)
AGENT_PARAMETERS = {
    "AZURE_ENV_NAME": "independent-agent",
    "AZURE_LOCATION": "westus",
    "AZURE_AI_PROJECT_ID": (
        "/subscriptions/sub-123/resourceGroups/rg-agent/providers/"
        "Microsoft.CognitiveServices/accounts/other-account/projects/other-project"
    ),
    "AZURE_AI_PROJECT_ENDPOINT": (
        "https://other-account.services.ai.azure.com/api/projects/other-project"
    ),
    "AZURE_AI_PROJECT_ACR_CONNECTION_NAME": "other-registry",
    "AZURE_CONTAINER_REGISTRY_ENDPOINT": "other.azurecr.io",
    "FOUNDRY_MODEL": "other-model",
    "TOOLBOX_MCP_URL": "https://tools.example/toolboxes/other-toolbox/mcp",
}

ENVIRONMENT_MOCK = """
operation = args[1] if len(args) > 1 and args[0] == "env" else ""
if args[:2] not in (
    ["extension", "install"], ["config", "set"], ["env", "new"], ["env", "set"]
):
    raise SystemExit(f"Unexpected azd command: {args}")
if operation and operation == os.environ.get("FAIL_OPERATION"):
    print(f"mock {operation} failed", file=sys.stderr)
    raise SystemExit(7)
"""

TOOLBOX_MOCK = """
if args == ["env", "get-value", "TOOLBOX_FIBEY_TOOLBOX_MCP_ENDPOINT"]:
    operation = "endpoint"
elif len(args) == 6 and args[:4] == ["ai", "toolbox", "publish", "fibey-toolbox"] and args[-1] == "--no-prompt":
    operation = "publish"
elif args == ["ai", "toolbox", "versions", "list", "fibey-toolbox", "--output", "json", "--no-prompt"]:
    operation = "list"
else:
    raise SystemExit(f"Unexpected azd command: {args}")
if operation == os.environ.get("FAIL_OPERATION"):
    print(f"mock {operation} failed", file=sys.stderr)
    raise SystemExit(7)
if operation == "endpoint":
    print(os.environ["TOOLBOX_ENDPOINT"])
elif operation == "list":
    print(os.environ["TOOLBOX_VERSIONS"])
"""


def write_executable(path: Path, source: str, interpreter: str = "python3") -> None:
    path.write_text(
        f"#!/usr/bin/env {interpreter}\n{textwrap.dedent(source).lstrip()}",
        encoding="utf-8",
    )
    path.chmod(0o755)


class ScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "commands.jsonl"
        self.export = self.root / "github-env"
        self.summary = self.root / "summary"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "COMMAND_LOG": str(self.log),
            "RUNNER_TEMP": str(self.root),
            "GITHUB_WORKSPACE": str(ROOT),
            "GITHUB_ENV": str(self.export),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "FAIL_OPERATION": "",
        }
        for name in ("az", "azd", "curl"):
            write_executable(
                self.bin / name,
                "import sys\nraise SystemExit(f'Unexpected command: {sys.argv}')\n",
            )

    def _mock_azd(self, body: str) -> None:
        prefix = """
            import json
            import os
            import sys
            from pathlib import Path

            args = sys.argv[1:]
            with Path(os.environ["COMMAND_LOG"]).open("a", encoding="utf-8") as log:
                log.write(json.dumps(args) + "\\n")
        """
        write_executable(self.bin / "azd", textwrap.dedent(prefix) + textwrap.dedent(body))

    def _run(self, command: list[str], cwd: Path | None = None, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=cwd or ROOT,
            env={**self.env, **env},
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def _initialize_agent(self, parameters: dict, failure: str = "") -> subprocess.CompletedProcess[str]:
        parameter_file = self.root / "agent-parameters.json"
        parameter_file.write_text(json.dumps(parameters), encoding="utf-8")
        project = self.root / "agent"
        project.mkdir(exist_ok=True)
        return self._run(
            ["bash", str(ROOT / ".github/scripts/initialize-deployment.sh")],
            PARAMETER_FILE=str(parameter_file),
            PROJECT_DIRECTORY=str(project),
            OPERATION="agent",
            AZURE_SUBSCRIPTION_ID="sub-123",
            AZURE_TENANT_ID="tenant-123",
            FAIL_OPERATION=failure,
        )

    def _toolbox_hook(self) -> str:
        self._mock_azd(TOOLBOX_MOCK)
        manifest = yaml.safe_load((ROOT / "foundation/azure.yaml").read_text(encoding="utf-8"))
        return manifest["services"]["fibey-toolbox"]["hooks"]["postdeploy"][0]["run"]

    def test_foundation_outputs_are_imported_without_exposing_values(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github/workflows/deploy-foundation.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        step = next(
            step for step in workflow["jobs"]["deploy"]["steps"]
            if "ARM_OUTPUTS" in step.get("env", {})
        )
        self._mock_azd(ENVIRONMENT_MOCK)
        (self.root / "foundation").mkdir()
        outputs = {
            "AZURE_ENV_NAME": "fibey-test",
            "AZURE_LOCATION": "eastus2",
            "KB_MCP_URL": "https://kb.example/mcp",
            "APPLICATIONINSIGHTS_CONNECTION_STRING": "SECRET=literal-$HOME",
            "INVENTORY_MCP_URL": "https://inventory.example/mcp",
            "WORK_ORDERS_API_URL": "https://orders.example",
        }

        for failure in ("", "new", "set", "invalid-json"):
            with self.subTest(failure=failure or "none"):
                self.log.unlink(missing_ok=True)
                result = self._run(
                    ["bash", "-c", step["run"]],
                    cwd=self.root,
                    ARM_OUTPUTS="{" if failure == "invalid-json" else json.dumps(outputs),
                    ENVIRONMENT_NAME="fibey-test",
                    DEPLOYMENT_LOCATION="eastus2",
                    AZURE_SUBSCRIPTION_ID="sub-test",
                    TOOLBOX_EXTENSION_VERSION="1.0.0-beta.12",
                    FAIL_OPERATION=failure,
                )
                self.assertNotIn("SECRET", result.stdout + result.stderr)
                calls = self._calls()
                if failure:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(result.stderr)
                    self.assertEqual(any(call[:2] == ["env", "set"] for call in calls), failure == "set")
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[:2] for call in calls],
                        [["extension", "install"], ["config", "set"], ["env", "new"], ["env", "set"]],
                    )
                    settings = dict(item.split("=", 1) for item in calls[-1][2:-4])
                    self.assertEqual(settings, outputs)

    def test_foundation_toolbox_uses_project_endpoint_outputs(self) -> None:
        workflow = yaml.load(
            (ROOT / ".github/workflows/deploy-foundation.yml").read_text(encoding="utf-8"),
            Loader=yaml.BaseLoader,
        )
        step = next(
            step for step in workflow["jobs"]["deploy"]["steps"]
            if "azd deploy fibey-toolbox --no-prompt" in step.get("run", "")
        )
        endpoint = "https://example.services.ai.azure.com/api/projects/test"
        outputs = {
            "AZURE_ENV_NAME": "fibey-test",
            "FOUNDRY_PROJECT_ENDPOINT": endpoint,
            "AZURE_AI_PROJECT_ENDPOINT": endpoint,
        }
        deploy_env = {
            key: outputs[value.removeprefix("${{ steps.infrastructure.outputs.").removesuffix(" }}")]
            for key, value in step["env"].items()
        }
        self._mock_azd("""
            if args != ["deploy", "fibey-toolbox", "--no-prompt"]:
                raise SystemExit(f"Unexpected azd command: {args}")
            for key in ("FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"):
                if os.environ.get(key) != os.environ["EXPECTED_PROJECT_ENDPOINT"]:
                    raise SystemExit(f"Incorrect project endpoint: {key}")
        """)
        for missing in ("", "FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"):
            with self.subTest(missing=missing or "none"):
                self.log.unlink(missing_ok=True)
                env = {**deploy_env, **({missing: ""} if missing else {})}
                result = self._run(
                    ["bash", "-c", step["run"]],
                    EXPECTED_PROJECT_ENDPOINT=endpoint,
                    **env,
                )
                if missing:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Foundation deployment did not output {missing}", result.stderr)
                    self.assertFalse(self.log.exists())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self._calls(), [["deploy", "fibey-toolbox", "--no-prompt"]])

    def test_toolbox_publishes_the_deployed_version(self) -> None:
        hook = self._toolbox_hook()
        result = self._run(
            ["sh", "-c", hook],
            TOOLBOX_ENDPOINT=ENDPOINT,
            TOOLBOX_VERSIONS='{"default_version":"2","versions":[{"version":"999"},{"version":"2"}]}',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [
            ["env", "get-value", "TOOLBOX_FIBEY_TOOLBOX_MCP_ENDPOINT"],
            ["ai", "toolbox", "publish", "fibey-toolbox", "2", "--no-prompt"],
            ["ai", "toolbox", "versions", "list", "fibey-toolbox", "--output", "json", "--no-prompt"],
        ])

    def test_toolbox_stops_on_invalid_endpoint_or_failed_promotion(self) -> None:
        hook = self._toolbox_hook()
        cases = (
            (ENDPOINT.replace("/versions/2/", "/versions/x/"), '{"default_version":"2"}', "", 1,
             "Expected the deployed fibey-toolbox version endpoint"),
            (ENDPOINT, '{"default_version":"2"}', "publish", 2, "mock publish failed"),
            (ENDPOINT, '{"default_version":"1"}', "", 3, "The deployed toolbox version is not the default"),
        )
        for endpoint, versions, failure, count, message in cases:
            with self.subTest(message=message):
                self.log.unlink(missing_ok=True)
                result = self._run(
                    ["sh", "-c", hook],
                    TOOLBOX_ENDPOINT=endpoint,
                    TOOLBOX_VERSIONS=versions,
                    FAIL_OPERATION=failure,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(len(self._calls()), count)

    def test_agent_initialization_uses_its_own_parameters(self) -> None:
        self._mock_azd(ENVIRONMENT_MOCK)
        result = self._initialize_agent(AGENT_PARAMETERS)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        self.assertEqual(calls[0][:3], ["env", "new", "independent-agent"])
        self.assertEqual(calls[1][:2], ["env", "set"])
        settings = dict(item.split("=", 1) for item in calls[1][2:-4])
        for key, value in AGENT_PARAMETERS.items():
            self.assertEqual(settings[key], value)
        self.assertEqual(settings["AZURE_RESOURCE_GROUP"], "rg-agent")
        self.assertEqual(
            settings["AZURE_AI_ACCOUNT_ID"],
            AGENT_PARAMETERS["AZURE_AI_PROJECT_ID"].rsplit("/projects/", 1)[0],
        )
        self.assertEqual(settings["AZURE_AI_ACCOUNT_NAME"], "other-account")
        self.assertEqual(settings["AZURE_AI_PROJECT_NAME"], "other-project")
        self.assertEqual(settings["AZURE_AI_FOUNDRY_PROJECT_ID"], AGENT_PARAMETERS["AZURE_AI_PROJECT_ID"])
        self.assertEqual(settings["FOUNDRY_PROJECT_ENDPOINT"], AGENT_PARAMETERS["AZURE_AI_PROJECT_ENDPOINT"])
        self.assertEqual(settings["AZURE_TENANT_ID"], "tenant-123")
        self.assertEqual(self.export.read_text(encoding="utf-8"), "AZURE_ENV_NAME=independent-agent\n")

    def test_agent_initialization_rejects_bad_inputs_and_azd_failures(self) -> None:
        self._mock_azd(ENVIRONMENT_MOCK)
        cases = (
            ({"TOOLBOX_MCP_URL": ""}, "", "agent parameters require"),
            ({"AZURE_AI_PROJECT_ID": AGENT_PARAMETERS["AZURE_AI_PROJECT_ID"].replace("sub-123", "other")},
             "", "authenticated subscription"),
            ({}, "set", "mock set failed"),
        )
        for overrides, failure, message in cases:
            with self.subTest(message=message):
                for path in (self.log, self.export, self.summary):
                    path.unlink(missing_ok=True)
                result = self._initialize_agent({**AGENT_PARAMETERS, **overrides}, failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(self.export.exists())
                self.assertFalse(self.summary.exists())
                if failure:
                    self.assertEqual(len(self._calls()), 2)
                else:
                    self.assertFalse(self.log.exists())

    def test_agent_deploy_wrapper_propagates_failures(self) -> None:
        self._mock_azd("""
            if args != ["deploy", "fibey-agent", "--no-prompt"]:
                raise SystemExit(f"Unexpected azd command: {args}")
            if os.environ.get("FAIL_OPERATION") == "deploy":
                print("mock deployment failed", file=sys.stderr)
                raise SystemExit(7)
            print("deployed")
        """)
        command = ["bash", str(ROOT / ".github/scripts/run-azd.sh")]
        env = {"PROJECT_DIRECTORY": str(self.root), "COMMAND": "deploy", "SERVICE": "fibey-agent"}
        result = self._run(command, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), [["deploy", "fibey-agent", "--no-prompt"]])

        result = self._run(command, FAIL_OPERATION="deploy", **env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Deployment of fibey-agent failed.", result.stderr)

    def test_local_knowledge_setup_is_opt_in_and_failure_propagates(self) -> None:
        project = self.root / "fibey project"
        for directory in ("scripts", "ui", "foundation/scripts"):
            (project / directory).mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/setup.sh", project / "scripts/setup.sh")
        (project / ".env.example").write_text("EXAMPLE=value\n", encoding="utf-8")
        setup_log = self.root / "setup.log"
        for name in ("uv", "npm"):
            write_executable(self.bin / name, """
                import os
                import sys
                from pathlib import Path

                with Path(os.environ["SETUP_LOG"]).open("a", encoding="utf-8") as log:
                    log.write(f"{Path(sys.argv[0]).name}|{os.getcwd()}|{' '.join(sys.argv[1:])}\\n")
            """)
        write_executable(project / "foundation/scripts/setup-knowledge-base.sh", """
            printf 'knowledge|%s\\n' "$PWD" >> "$SETUP_LOG"
            if [[ "${KNOWLEDGE_EXIT:-0}" != 0 ]]; then
              echo "knowledge-base setup failed" >&2
              exit "$KNOWLEDGE_EXIT"
            fi
        """, "bash")
        command = ["bash", str(project / "scripts/setup.sh")]
        env = {"SETUP_LOG": str(setup_log)}

        result = self._run(command, cwd=self.root, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(setup_log.read_text().splitlines(), [
            f"uv|{project}|sync",
            f"npm|{project / 'ui'}|install",
        ])
        self.assertEqual((project / ".env").read_text(), "EXAMPLE=value\n")

        (project / ".env").write_text("EXISTING=value\n", encoding="utf-8")
        setup_log.unlink()
        result = self._run(command + ["--setup-knowledge-base"], cwd=self.root, **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(setup_log.read_text().splitlines(), [
            f"uv|{project}|sync",
            f"npm|{project / 'ui'}|install",
            f"knowledge|{project / 'foundation'}",
        ])
        self.assertEqual((project / ".env").read_text(), "EXISTING=value\n")

        setup_log.unlink()
        result = self._run(
            command + ["--setup-knowledge-base"], cwd=self.root, KNOWLEDGE_EXIT="9", **env
        )
        self.assertEqual(result.returncode, 9)
        self.assertIn("knowledge-base setup failed", result.stderr)
        self.assertNotIn("=== Setup complete ===", result.stdout)
        self.assertEqual(setup_log.read_text().splitlines().count(f"knowledge|{project / 'foundation'}"), 1)

    def test_knowledge_setup_waits_for_current_indexer_success(self) -> None:
        write_executable(self.bin / "az", """
            import sys
            args = sys.argv[1:]
            if args[:3] == ["storage", "account", "show"]:
                print("/subscriptions/test/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/storage")
            elif args[:3] != ["storage", "blob", "upload-batch"]:
                raise SystemExit(f"Unexpected az command: {args}")
        """)
        write_executable(self.bin / "curl", """
            import json
            import os
            import sys
            from pathlib import Path

            urls = [arg for arg in sys.argv[1:] if arg.startswith("https://")]
            if len(urls) != 1:
                raise SystemExit(f"Unexpected curl arguments: {sys.argv[1:]}")
            url = urls[0]
            with Path(os.environ["CURL_LOG"]).open("a", encoding="utf-8") as log:
                log.write(url + "\\n")
            if "search.status" in url:
                counter = Path(os.environ["STATUS_COUNT"])
                count = int(counter.read_text()) if counter.exists() else 0
                responses = json.loads(os.environ["INDEXER_RESPONSES"])
                print(json.dumps(responses[count]))
                counter.write_text(str(count + 1))
        """)
        write_executable(self.bin / "sleep", "raise SystemExit(0)\n")
        curl_log = self.root / "curl.log"
        counter = self.root / "status-count"
        old = {
            "status": "running",
            "lastResult": {
                "status": "success", "startTime": "old", "endTime": "old",
                "itemsProcessed": 3, "itemsFailed": 0,
            },
        }
        current = deepcopy(old)
        current["lastResult"].update({"startTime": "new", "endTime": "new"})
        failed = {"status": "error", "lastResult": {"status": "transientFailure"}}
        env = {
            "CURL_LOG": str(curl_log),
            "STATUS_COUNT": str(counter),
            "AZURE_RESOURCE_GROUP": "rg",
            "STORAGE_ACCOUNT": "storage",
            "SEARCH_SERVICE": "search",
            "AZURE_SEARCH_INDEX": "index",
            "KB_NAME": "kb",
            "KS_NAME": "source",
            "AZURE_OPENAI_ENDPOINT": "https://model.example/",
            "FOUNDRY_MODEL": "model",
            "AZURE_SEARCH_ADMIN_KEY": "test-key",
            "GITHUB_ACTIONS": "false",
        }
        command = ["bash", str(ROOT / "foundation/scripts/setup-knowledge-base.sh")]
        for succeeds, responses in ((True, [old, old, current]), (False, [old, failed])):
            with self.subTest(succeeds=succeeds):
                curl_log.unlink(missing_ok=True)
                counter.unlink(missing_ok=True)
                result = self._run(command, INDEXER_RESPONSES=json.dumps(responses), **env)
                urls = curl_log.read_text().splitlines()
                if succeeds:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("Indexer succeeded: processed 3 item(s), failed 0.", result.stdout)
                    status = [i for i, url in enumerate(urls) if "search.status" in url]
                    run = next(i for i, url in enumerate(urls) if "search.run" in url)
                    source = next(i for i, url in enumerate(urls) if "/knowledgesources/" in url)
                    knowledge = next(i for i, url in enumerate(urls) if "/knowledgebases/" in url)
                    self.assertEqual(len(status), 3)
                    self.assertLess(status[0], run)
                    self.assertLess(run, status[1])
                    self.assertLess(status[2], source)
                    self.assertLess(source, knowledge)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("The indexer entered an error state", result.stdout)
                    self.assertFalse(any("/knowledgesources/" in url or "/knowledgebases/" in url for url in urls))

    def test_prerequisite_url_update_preserves_other_parameters(self) -> None:
        script = (ROOT / "scripts/deploy-prerequisites.sh").read_text(encoding="utf-8")
        match = re.search(r"'(\.parameters\.inventoryMcpUrl\.value = [^']+)'", script)
        self.assertIsNotNone(match)
        assert match is not None
        parameters = json.loads((ROOT / "foundation/infra/main.parameters.json").read_text())
        inventory = "https://new-inventory.example/mcp?api-version=v1"
        orders = "https://new-orders.example"
        result = subprocess.run(
            ["jq", "--arg", "inventory_url", inventory, "--arg", "work_orders_url", orders, match[1]],
            input=json.dumps(parameters),
            capture_output=True,
            text=True,
            check=True,
        )
        expected = deepcopy(parameters)
        expected["parameters"]["inventoryMcpUrl"]["value"] = inventory
        expected["parameters"]["workOrdersApiUrl"]["value"] = orders
        self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
