from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MUTATION_COMMAND = re.compile(
    r"\bazd\s+(?:up|deploy)\b|\bazd\s+provision(?!\s+--preview\b)\b"
)
SCRIPT_REFERENCE = re.compile(r"\.github/scripts/([\w.-]+\.sh)")


def workflow(name: str) -> dict[str, object]:
    path = ROOT / ".github/workflows" / name
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    if not isinstance(document, dict):
        raise AssertionError(f"{path} did not contain a YAML mapping.")
    return document


def job(workflow_name: str, job_name: str) -> dict[str, object]:
    jobs = workflow(workflow_name).get("jobs")
    if not isinstance(jobs, dict) or not isinstance(jobs.get(job_name), dict):
        raise AssertionError(f"{workflow_name} has no {job_name!r} job.")
    return jobs[job_name]


def run_commands(job_definition: dict[str, object]) -> str:
    """Return inline run steps plus the contents of any repository scripts they call."""
    steps = job_definition.get("steps")
    if not isinstance(steps, list):
        raise AssertionError("Workflow job has no steps.")
    inline = "\n".join(
        step["run"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    )
    # Expand the shared deploy action into the commands its script runs for each service.
    for step in steps:
        if isinstance(step, dict) and step.get("uses") == "./.github/actions/run-azd":
            if step["with"].get("command") == "provision":
                inline += "\nazd provision --no-prompt"
                continue
            service = step["with"]["service"]
            inline += f"\nazd deploy {service} --no-prompt"
            if step["with"].get("verify-promotion") == "true":
                inline += f"\nPromoted {service} version $GITHUB_STEP_SUMMARY"
    scripts = "\n".join(
        (ROOT / ".github/scripts" / name).read_text(encoding="utf-8")
        for name in sorted(set(SCRIPT_REFERENCE.findall(inline)))
    )
    return f"{inline}\n{scripts}"


class WorkflowContractTests(unittest.TestCase):
    def test_pr_validation_is_stable_read_only_and_path_selected(self) -> None:
        document = workflow("validate.yml")
        event = document["on"]
        self.assertIsInstance(event, dict)
        self.assertIn("pull_request", event)
        self.assertNotIn("paths", event["pull_request"] or {})
        self.assertEqual(document["permissions"], {"contents": "read"})
        self.assertEqual(set(document["jobs"]), {"validate"})
        validate_job = job("validate.yml", "validate")
        self.assertEqual(validate_job["name"], "Validate deployment changes")
        self.assertEqual(validate_job["permissions"], {"contents": "read"})
        steps = validate_job["steps"]
        self.assertIsInstance(steps, list)
        step_text = "\n".join(
            str(step.get("run", "")) for step in steps if isinstance(step, dict)
        )
        self.assertNotRegex(step_text, MUTATION_COMMAND)
        self.assertIn("select_changed_paths.py", step_text)

    def test_manual_workflows_use_separate_protected_environments(self) -> None:
        expected_environments = {
            "deploy-foundation.yml": "fibey-foundation",
            "deploy-agent.yml": "fibey-agent",
        }
        for name, environment_name in expected_environments.items():
            with self.subTest(workflow=name):
                document = workflow(name)
                self.assertIn("workflow_dispatch", document["on"])
                self.assertNotIn("pull_request", document)
                arm_deployment_input = document["on"]["workflow_dispatch"]["inputs"][
                    "arm_deployment_name"
                ]
                self.assertEqual(arm_deployment_input["required"], "false")
                self.assertEqual(arm_deployment_input["default"], "")
                self.assertEqual(
                    document["concurrency"],
                    {
                        "group": "fibey-jf-1026-azure-mutation",
                        "cancel-in-progress": "false",
                    },
                )
                jobs = document["jobs"]
                self.assertIsInstance(jobs, dict)
                for definition in jobs.values():
                    self.assertIsInstance(definition, dict)
                    self.assertEqual(definition["environment"], environment_name)
                    self.assertEqual(
                        definition["permissions"],
                        {"contents": "read", "id-token": "write"},
                    )
                    steps = definition["steps"]
                    self.assertTrue(steps[0]["uses"].startswith("actions/checkout@"))
                    self.assertEqual(steps[0]["with"]["persist-credentials"], "false")
                    self.assertEqual(
                        steps[1]["uses"], "./.github/actions/setup-deployment"
                    )
                    self.assertNotIn("rm -rf", run_commands(definition))
                    self.assertNotIn("azd up", run_commands(definition))

    def test_safe_defaults_and_operation_specific_deploys(self) -> None:
        foundation = workflow("deploy-foundation.yml")
        agent = workflow("deploy-agent.yml")
        self.assertEqual(
            foundation["on"]["workflow_dispatch"]["inputs"]["operation"]["default"],
            "preview",
        )
        self.assertEqual(
            agent["on"]["workflow_dispatch"]["inputs"]["operation"]["default"],
            "preflight",
        )

        foundation_jobs = foundation["jobs"]
        self.assertEqual(set(foundation_jobs), {"preview", "provision-and-toolbox"})
        self.assertEqual(
            foundation["on"]["workflow_dispatch"]["inputs"]["operation"]["options"],
            ["preview", "provision-and-toolbox"],
        )
        agent_jobs = agent["jobs"]
        preview = run_commands(foundation_jobs["preview"])
        provision = run_commands(foundation_jobs["provision-and-toolbox"])
        agent_preflight = run_commands(agent_jobs["preflight"])
        agent_deploy = run_commands(agent_jobs["deploy"])

        self.assertIn("azd provision --preview --no-prompt", preview)
        self.assertNotRegex(preview, MUTATION_COMMAND)
        self.assertNotIn("azd ai toolbox publish", preview)
        self.assertNotRegex(agent_preflight, MUTATION_COMMAND)
        self.assertIn("azd provision --no-prompt", provision)
        self.assertIn("azd deploy fibey-toolbox --no-prompt", provision)
        self.assertIn("Promoted fibey-toolbox version", provision)
        self.assertIn("$GITHUB_STEP_SUMMARY", provision)
        self.assertNotIn("azd deploy fibey-agent", provision)
        lifecycle_steps = [
            step["with"]
            for step in foundation_jobs["provision-and-toolbox"]["steps"]
            if step.get("uses") == "./.github/actions/run-azd"
        ]
        self.assertEqual(
            lifecycle_steps,
            [
                {"project-directory": "foundation", "command": "provision"},
                {
                    "project-directory": "foundation",
                    "service": "fibey-toolbox",
                    "verify-promotion": "true",
                },
            ],
        )
        self.assertNotIn("accepted_preview_sha", str(foundation))
        self.assertNotIn("provision-preflight", foundation["jobs"])
        self.assertIn("azd deploy fibey-agent --no-prompt", agent_deploy)
        self.assertIn("azd ai agent show fibey-agent", agent_deploy)
        self.assertIn("azd ai agent invoke", agent_deploy)
        self.assertIn("--new-session", agent_deploy)
        self.assertIn("FIB-002", agent_deploy)
        self.assertNotIn("azd provision", agent_deploy)
        self.assertNotIn("azd deploy fibey-toolbox", agent_deploy)

    def test_workflows_install_only_the_extension_for_their_lifecycle(self) -> None:
        expected = {
            "deploy-foundation.yml": ("azure.ai.toolboxes", "1.0.0-beta.12"),
            "deploy-agent.yml": ("azure.ai.agents", "1.0.0-beta.22"),
        }
        for workflow_name, (extension, version) in expected.items():
            with self.subTest(workflow=workflow_name):
                jobs = workflow(workflow_name)["jobs"]
                self.assertIsInstance(jobs, dict)
                for definition in jobs.values():
                    steps = definition["steps"]
                    setup_steps = [
                        step for step in steps
                        if isinstance(step, dict)
                        and step.get("uses") == "./.github/actions/setup-deployment"
                    ]
                    self.assertEqual(len(setup_steps), 1)
                    for step in setup_steps:
                        self.assertEqual(
                            step["with"],
                            {
                                "client-id": "${{ secrets.AZURE_CLIENT_ID }}",
                                "tenant-id": "${{ secrets.AZURE_TENANT_ID }}",
                                "subscription-id": "${{ secrets.AZURE_SUBSCRIPTION_ID }}",
                                "extension-name": extension,
                                "extension-version": version,
                            },
                        )

    def test_shared_setup_owns_oidc_and_azd_authentication(self) -> None:
        action_path = ROOT / ".github/actions/setup-deployment/action.yml"
        action = yaml.load(action_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = action["runs"]["steps"]
        login = steps[0]
        self.assertEqual(
            login["uses"],
            "Azure/login@93381592711f247e165c389ebb30b596c84cdc48",
        )
        self.assertEqual(
            login["with"],
            {
                "client-id": "${{ inputs.client-id }}",
                "tenant-id": "${{ inputs.tenant-id }}",
                "subscription-id": "${{ inputs.subscription-id }}",
            },
        )
        self.assertEqual(
            run_commands(action["runs"]).count("azd config set auth.useAzCliAuth true"),
            1,
        )
        context_path = ROOT / ".github/actions/load-foundation-context/action.yml"
        context = yaml.load(context_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertNotIn("azd config set", run_commands(context["runs"]))
        self.assertNotIn("azd env select", run_commands(context["runs"]))
        ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/foundation/.azure/", ignore_rules)
        self.assertIn("/agent/.azure/", ignore_rules)

    def test_local_actions_are_declared_and_inputs_are_supplied(self) -> None:
        for workflow_name in ("deploy-foundation.yml", "deploy-agent.yml"):
            document = workflow(workflow_name)
            jobs = document["jobs"]
            for definition in jobs.values():
                steps = definition["steps"]
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    action_reference = step.get("uses", "")
                    if not isinstance(action_reference, str) or not action_reference.startswith(
                        "./.github/actions/"
                    ):
                        continue
                    action_path = ROOT / action_reference.removeprefix("./") / "action.yml"
                    self.assertTrue(action_path.is_file())
                    action = yaml.load(
                        action_path.read_text(encoding="utf-8"),
                        Loader=yaml.BaseLoader,
                    )
                    inputs = action.get("inputs", {})
                    provided = step.get("with", {})
                    self.assertIsInstance(inputs, dict)
                    self.assertIsInstance(provided, dict)
                    required = {
                        name for name, definition in inputs.items()
                        if definition.get("required") == "true"
                    }
                    self.assertLessEqual(required, set(provided))
                    self.assertLessEqual(set(provided), set(inputs))

                    action_text = action_path.read_text(encoding="utf-8")
                    referenced = {
                        match.group(1)
                        for match in re.finditer(
                            r"\$\{\{\s*inputs\.([\w-]+)", action_text
                        )
                    }
                    self.assertEqual(referenced, set(inputs))

    def test_foundation_role_assignment_uses_service_principal_type(self) -> None:
        foundation = workflow("deploy-foundation.yml")
        for job_name in ("preview", "provision-and-toolbox"):
            with self.subTest(job=job_name):
                steps = foundation["jobs"][job_name]["steps"]
                context_steps = [
                    step for step in steps
                    if isinstance(step, dict)
                    and step.get("uses") == "./.github/actions/load-foundation-context"
                ]
                self.assertEqual(len(context_steps), 1)
                self.assertEqual(
                    context_steps[0]["with"]["principal-type"],
                    "${{ vars.AZURE_PRINCIPAL_TYPE || 'ServicePrincipal' }}",
                )

    def test_referenced_deployment_scripts_are_executable_and_strict(self) -> None:
        sources = [
            path.read_text(encoding="utf-8")
            for path in [
                *(ROOT / ".github/workflows").glob("*.yml"),
                *(ROOT / ".github/actions").rglob("action.yml"),
            ]
        ]
        referenced = {
            name for source in sources for name in SCRIPT_REFERENCE.findall(source)
        }
        self.assertEqual(
            referenced,
            {path.name for path in (ROOT / ".github/scripts").glob("*.sh")},
        )
        for name in referenced:
            with self.subTest(script=name):
                path = ROOT / ".github/scripts" / name
                self.assertTrue(os.access(path, os.X_OK))
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("#!/usr/bin/env bash\n"))
                self.assertIn("set -euo pipefail", text)


if __name__ == "__main__":
    unittest.main()
