from __future__ import annotations

import glob
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml

from select_changed_paths import select_groups


ROOT = Path(__file__).resolve().parents[2]


def load_workflow(name: str) -> dict:
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def find_action(steps: list[dict], name: str) -> dict:
    return next(step for step in steps if step.get("uses", "").startswith(name))


class WorkflowTests(unittest.TestCase):
    def test_foundation_build_deploy_and_output_handoff(self) -> None:
        workflow = load_workflow("deploy-foundation.yml")
        self.assertEqual(set(workflow["jobs"]), {"build", "deploy"})
        build = workflow["jobs"]["build"]
        deploy = workflow["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], "build")

        for job, operation in ((build, "whatIf"), (deploy, "create")):
            with self.subTest(operation=operation):
                self.assertEqual(job.get("environment"), "fibey-foundation")
                self.assertEqual(job["permissions"]["id-token"], "write")
                login = find_action(job["steps"], "Azure/login@")
                for name in ("client", "tenant", "subscription"):
                    self.assertEqual(
                        login["with"][f"{name}-id"],
                        "${{ secrets.AZURE_" + name.upper() + "_ID }}",
                    )
                deployment = find_action(job["steps"], "Azure/bicep-deploy@")
                self.assertEqual(deployment["with"]["operation"], operation)
                self.assertEqual(deployment["with"]["template-file"], "foundation/infra/main.bicep")
                self.assertEqual(
                    deployment["with"]["parameters-file"],
                    "foundation/infra/main.parameters.json",
                )

        upload = find_action(build["steps"], "actions/upload-artifact@")
        download = find_action(deploy["steps"], "actions/download-artifact@")
        self.assertEqual(upload["with"]["name"], download["with"]["name"])
        self.assertEqual(
            set(upload["with"]["path"].splitlines()),
            {"foundation/infra/**", "foundation/azure.yaml"},
        )
        self.assertEqual(download["with"]["path"], "${{ github.workspace }}/foundation")
        self.assertEqual(build["outputs"]["location"], "${{ steps.location.outputs.location }}")
        location = next(step for step in build["steps"] if step.get("id") == "location")
        self.assertIn(".parameters.location.value", location["run"])
        self.assertIn("foundation/infra/main.parameters.json", location["run"])
        self.assertEqual(
            find_action(deploy["steps"], "Azure/bicep-deploy@")["with"]["location"],
            "${{ needs.build.outputs.location }}",
        )
        self.assertLess(
            build["steps"].index(find_action(build["steps"], "Azure/bicep-deploy@")),
            build["steps"].index(upload),
        )

        steps = deploy["steps"]
        infrastructure = next(i for i, step in enumerate(steps) if step.get("id") == "infrastructure")
        handoff = next(i for i, step in enumerate(steps) if "ARM_OUTPUTS" in step.get("env", {}))
        toolbox = next(
            i for i, step in enumerate(steps)
            if "azd deploy fibey-toolbox --no-prompt" in step.get("run", "")
        )
        self.assertLess(infrastructure, handoff)
        self.assertLess(handoff, toolbox)
        self.assertEqual(
            steps[infrastructure]["with"]["masked-outputs"], "APPLICATIONINSIGHTS_CONNECTION_STRING"
        )
        self.assertEqual(steps[handoff]["env"]["ARM_OUTPUTS"], "${{ toJSON(steps.infrastructure.outputs) }}")
        self.assertEqual(
            steps[toolbox]["env"]["AZURE_ENV_NAME"],
            "${{ steps.infrastructure.outputs.AZURE_ENV_NAME }}",
        )
        for endpoint in ("FOUNDRY_PROJECT_ENDPOINT", "AZURE_AI_PROJECT_ENDPOINT"):
            self.assertEqual(
                steps[toolbox]["env"][endpoint],
                "${{ steps.infrastructure.outputs." + endpoint + " }}",
            )
        self.assertNotIn("setup-knowledge-base.sh", "\n".join(step.get("run", "") for step in steps))

    def test_foundation_artifact_restores_relative_dependencies(self) -> None:
        workflow = load_workflow("deploy-foundation.yml")
        upload = find_action(workflow["jobs"]["build"]["steps"], "actions/upload-artifact@")
        paths = upload["with"]["path"].splitlines()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root / "build"
            deploy = root / "deploy/foundation"
            shutil.copytree(
                ROOT / "foundation/infra",
                build / "foundation/infra",
                ignore=shutil.ignore_patterns("main.json"),
            )
            shutil.copy2(ROOT / "foundation/azure.yaml", build / "foundation/azure.yaml")
            prefixes = [build / path.split("*", 1)[0].rstrip("/") for path in paths]
            upload_root = Path(os.path.commonpath(prefixes))
            self.assertEqual(upload_root, build / "foundation")
            for path in paths:
                for match in glob.glob(str(build / path), recursive=True):
                    source = Path(match)
                    if source.is_file():
                        destination = deploy / source.relative_to(upload_root)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)

            for path in (
                "infra/main.bicep",
                "infra/main.parameters.json",
                "infra/resources.bicep",
                "infra/core/ai/ai-project.bicep",
                "infra/abbreviations.json",
                "azure.yaml",
            ):
                self.assertTrue((deploy / path).is_file(), path)
                self.assertEqual(
                    (deploy / path).read_bytes(),
                    (ROOT / "foundation" / path).read_bytes(),
                    path,
                )
            self.assertFalse((deploy / "infra/main.json").exists())
            self.assertFalse((deploy / "scripts").exists())

    def test_agent_build_deploy_uses_fixed_parameters_and_environment_auth(self) -> None:
        workflow = load_workflow("deploy-agent.yml")
        self.assertEqual(workflow["on"]["workflow_dispatch"], {})
        self.assertEqual(set(workflow["jobs"]), {"build", "deploy"})
        self.assertEqual(workflow["concurrency"], load_workflow("deploy-foundation.yml")["concurrency"])
        build = workflow["jobs"]["build"]
        deploy = workflow["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], "build")
        self.assertNotIn("environment", build)
        self.assertNotIn("id-token", build.get("permissions", {}))
        self.assertNotIn("id-token", workflow["permissions"])
        self.assertFalse(any(
            step.get("uses", "").startswith(("Azure/", "./")) for step in build["steps"]
        ))
        validation = next(step for step in build["steps"] if "--validate" in step.get("run", ""))
        self.assertEqual(validation["env"]["PARAMETER_FILE"], "agent/parameters.json")
        upload = find_action(build["steps"], "actions/upload-artifact@")
        self.assertLess(build["steps"].index(validation), build["steps"].index(upload))
        self.assertEqual(upload["with"]["if-no-files-found"], "error")
        self.assertEqual(upload["with"]["include-hidden-files"], "true")
        self.assertEqual(set(upload["with"]["path"].splitlines()), {
            "Dockerfile.agent", ".dockerignore", "pyproject.toml", "uv.lock",
            "src/fibey/**", "agent/azure.yaml", "agent/parameters.json",
            ".github/scripts/initialize-agent.sh",
        })

        self.assertEqual(deploy["environment"], "fibey-agent")
        self.assertEqual(deploy["permissions"]["id-token"], "write")
        steps = deploy["steps"]
        download = find_action(steps, "actions/download-artifact@")
        self.assertEqual(download["with"]["name"], upload["with"]["name"])
        self.assertEqual(download["with"]["path"], "${{ github.workspace }}")
        self.assertFalse(any(step.get("uses", "").startswith("actions/checkout@") for step in steps))
        login = find_action(steps, "Azure/login@")
        for name in ("client", "tenant", "subscription"):
            self.assertEqual(
                login["with"][f"{name}-id"],
                "${{ secrets.AZURE_" + name.upper() + "_ID }}",
            )
        setup = find_action(steps, "Azure/setup-azd@")
        self.assertEqual(setup["with"]["version"], "${{ env.AZD_VERSION }}")
        initialize = next(
            step for step in steps if step.get("run") == "bash .github/scripts/initialize-agent.sh"
        )
        self.assertEqual(initialize["env"]["PARAMETER_FILE"], "agent/parameters.json")
        self.assertEqual(initialize["env"]["PROJECT_DIRECTORY"], "agent")
        deployment = next(step for step in steps if step.get("run") == "azd deploy fibey-agent --no-prompt")
        smoke = next(step for step in steps if "azd ai agent show" in step.get("run", ""))
        ordered = [download, login, setup, initialize, deployment, smoke]
        self.assertEqual([steps.index(step) for step in ordered], sorted(steps.index(step) for step in ordered))
        for step in (deployment, smoke):
            self.assertEqual(step["working-directory"], "agent")
            self.assertNotIn("if", step)
            self.assertNotIn("continue-on-error", step)
        for job in workflow["jobs"].values():
            self.assertNotIn("if", job)
            for step in job["steps"]:
                if "uses" in step:
                    self.assertRegex(step["uses"], r"@v\d+\.\d+\.\d+$")

    def test_agent_source_artifact_restores_the_root_build_context(self) -> None:
        workflow = load_workflow("deploy-agent.yml")
        upload = find_action(workflow["jobs"]["build"]["steps"], "actions/upload-artifact@")
        paths = upload["with"]["path"].splitlines()
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory).resolve() / "build"
            deploy = Path(directory).resolve() / "deploy"
            shutil.copytree(
                ROOT / "src/fibey", build / "src/fibey",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            for relative in paths:
                if "*" not in relative:
                    destination = build / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / relative, destination)
            for relative in (".env", "agent/.azure/test/.env", "ui/index.html"):
                destination = build / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text("not deployment input", encoding="utf-8")
            prefixes = [build / path.split("*", 1)[0].rstrip("/") for path in paths]
            upload_root = Path(os.path.commonpath(prefixes))
            self.assertEqual(upload_root, build)
            for pattern in paths:
                for match in glob.glob(str(build / pattern), recursive=True):
                    source = Path(match)
                    if source.is_file():
                        destination = deploy / source.relative_to(upload_root)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
            for relative in (
                "pyproject.toml", "uv.lock", ".dockerignore", "agent/parameters.json",
                ".github/scripts/initialize-agent.sh", "src/fibey/agent/hosted.py",
                "src/fibey/agent/prompts/system_prompt.md",
            ):
                self.assertTrue((deploy / relative).is_file(), relative)
            manifest = yaml.safe_load((deploy / "agent/azure.yaml").read_text(encoding="utf-8"))
            service = manifest["services"]["fibey-agent"]
            project = deploy / "agent" / service["project"]
            self.assertEqual((project / service["docker"]["context"]).resolve(), deploy)
            self.assertEqual(
                (project / service["docker"]["path"]).resolve(), deploy / "Dockerfile.agent",
            )
            self.assertTrue((deploy / "Dockerfile.agent").is_file())
            for relative in (".env", "agent/.azure", "ui", "foundation", "services"):
                self.assertFalse((deploy / relative).exists(), relative)

    def test_changed_paths_select_relevant_validation_groups(self) -> None:
        all_groups = {"workflows", "foundation", "agent", "ui", "configuration"}
        cases = {
            ".github/workflows/deploy-agent.yml": {"workflows", "configuration"},
            "scripts/setup.sh": {"workflows", "configuration"},
            "scripts/deploy-prerequisites.sh": {"workflows", "configuration"},
            "foundation/infra/main.bicep": {"foundation", "configuration"},
            "services/foundry-iq-docs/docs/safety.md": {"foundation", "configuration"},
            "services/infra/main.bicep": {"foundation", "configuration"},
            "src/fibey/agent/hosted.py": {"agent", "configuration"},
            "agent/parameters.json": {"agent", "configuration"},
            "Dockerfile.agent": {"agent", "configuration"},
            "ui/Dockerfile": {"ui", "configuration"},
            "tests/deployment/test_workflows.py": all_groups,
            "docs/deployment.md": set(),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                selected = select_groups([path])
                self.assertEqual({name for name, enabled in selected.items() if enabled}, expected)

    def test_foundation_principal_defaults_handle_missing_user_identity(self) -> None:
        template = (ROOT / "foundation/infra/main.bicep").read_text(encoding="utf-8")
        self.assertIn("param principalId string = deployer().objectId", template)
        principal_type = next(line for line in template.splitlines() if line.startswith("param principalType "))
        self.assertEqual(
            principal_type,
            "param principalType string = empty(deployer().?userPrincipalName) "
            "? 'ServicePrincipal' : 'User'",
        )

    def test_container_identity_and_private_gateway(self) -> None:
        registry = (ROOT / "foundation/infra/modules/container-registry.bicep").read_text()
        container = (ROOT / "services/infra/modules/container-app.bicep").read_text()
        services = (ROOT / "services/infra/main.bicep").read_text()
        ui_gateway = (ROOT / "services/infra/fibey-ui-gateway.bicep").read_text()
        gateway = ui_gateway.split("module gateway ", 1)[1].split(
            "resource foundryAgentConsumerAssignment ", 1
        )[0]
        ui = ui_gateway.split("module ui ", 1)[1]

        self.assertIn("adminUserEnabled: false", registry)
        self.assertIn("identity: registryIdentityResourceId", container)
        self.assertNotIn("registryPassword", container)
        self.assertIn("roleDefinitionId: acrPullRoleDefinitionId", services)
        self.assertIn("externalIngress: false", gateway)
        self.assertIn("externalIngress: true", ui)
        self.assertIn("'https://${gateway.outputs.fqdn}/api/'", ui)


if __name__ == "__main__":
    unittest.main()
