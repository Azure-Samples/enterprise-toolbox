from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".github/scripts"


class DeploymentScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.summary = self.root / "summary.md"
        self.output = self.root / "output.txt"

    def _mock_azd(self, body: str) -> None:
        path = self.bin / "azd"
        path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def _run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            {
                "PATH": f"{self.bin}{os.pathsep}{environment['PATH']}",
                "RUNNER_TEMP": str(self.root),
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "GITHUB_OUTPUT": str(self.output),
                **env,
            }
        )
        return subprocess.run(
            ["bash", str(SCRIPTS / script), *args],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_preview_runs_only_the_what_if(self) -> None:
        self._mock_azd('[ "$*" = "provision --preview --no-prompt" ] || exit 9\necho what-if')
        result = self._run("azd-preview.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("what-if", result.stdout)

    def test_preview_failure_stops_without_summary(self) -> None:
        self._mock_azd("echo boom; exit 4")
        result = self._run("azd-preview.sh")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.summary.exists())

    def test_service_deploy_verifies_promotion_only_when_requested(self) -> None:
        env = {"PROJECT_DIRECTORY": str(self.root), "COMMAND": "deploy", "SERVICE": "fibey-toolbox"}
        self._mock_azd('[ "$*" = "deploy fibey-toolbox --no-prompt" ] || exit 9\necho "Promoted fibey-toolbox version 3 (id-3)."')
        result = self._run("run-azd.sh", VERIFY_PROMOTION="true", **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version 3", self.summary.read_text(encoding="utf-8"))

        self.summary.unlink()
        self._mock_azd("echo deployed")
        result = self._run("run-azd.sh", VERIFY_PROMOTION="true", **env)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.summary.exists())
        result = self._run("run-azd.sh", VERIFY_PROMOTION="false", **env)
        self.assertEqual(result.returncode, 0, result.stderr)

        self._mock_azd("exit 5")
        result = self._run("run-azd.sh", VERIFY_PROMOTION="false", **env)
        self.assertEqual(result.returncode, 1)

    def test_provision_suppresses_output_and_rejects_bad_input(self) -> None:
        base = {"PROJECT_DIRECTORY": str(self.root), "COMMAND": "provision"}
        self._mock_azd('[ "$*" = "provision --no-prompt" ] || exit 9\necho SECRET-VALUE')
        result = self._run("run-azd.sh", **base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("SECRET-VALUE", result.stdout + result.stderr)
        self._mock_azd("echo SECRET-VALUE; exit 5")
        result = self._run("run-azd.sh", **base)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("SECRET-VALUE", result.stdout + result.stderr)
        self.assertEqual(self._run("run-azd.sh", VERIFY_PROMOTION="true", **base).returncode, 2)
        self.assertEqual(self._run("run-azd.sh", PROJECT_DIRECTORY=str(self.root), COMMAND="up").returncode, 2)

    def test_bootstrap_validation_lists_missing_values(self) -> None:
        result = self._run("validate-bootstrap.sh", REQUIRE_TOOLBOX_ENDPOINTS="true")
        self.assertEqual(result.returncode, 1)
        self.assertIn("AZURE_SUBSCRIPTION_ID", result.stderr)
        self.assertIn("INVENTORY_MCP_URL", result.stderr)

    def test_agent_smoke_uses_new_session_read_only_prompt(self) -> None:
        log = self.root / "azd.log"
        self._mock_azd(f'printf "%s\\n" "$*" >> "{log}"')
        result = self._run("agent-smoke.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "ai agent show fibey-agent --no-prompt --output json")
        self.assertIn("--new-session", lines[1])
        self.assertIn("FIB-002", lines[1])


if __name__ == "__main__":
    unittest.main()
