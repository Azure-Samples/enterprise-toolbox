from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "foundation" / "scripts" / "resolve-deployment-context.py"
SPEC = importlib.util.spec_from_file_location("resolve_deployment_context", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)


def _output(value: str) -> dict[str, str]:
    return {"type": "String", "value": value}


def arm_deployment(
    *,
    subscription: str = "sub-123",
    resource_group: str = "rg-fibey-jf-1026",
    name: str = "selected-deployment",
    state: str = "Succeeded",
    timestamp: str = "2026-10-28T10:00:00Z",
) -> dict[str, Any]:
    account_id = (
        f"/subscriptions/{subscription}/resourceGroups/{resource_group}"
        "/providers/Microsoft.CognitiveServices/accounts/ai-account-sidzt6mr5x5qu"
    )
    project_id = f"{account_id}/projects/fibey-project"
    outputs = {
        "AZURE_TENANT_ID": _output("tenant-123"),
        "AZURE_RESOURCE_GROUP": _output(resource_group),
        "AZURE_AI_ACCOUNT_ID": _output(account_id),
        "AzureAiProjectId": _output(project_id),
        "AzureAiAccountName": _output("ai-account-sidzt6mr5x5qu"),
        "AZURE_AI_PROJECT_NAME": _output("fibey-project"),
        "aZuRe_aI_pRoJeCt_EnDpOiNt": _output(
            "https://example.services.ai.azure.com/api/projects/fibey-project"
        ),
        "FOUNDRY_PROJECT_ENDPOINT": _output(
            "https://example.services.ai.azure.com/api/projects/fibey-project"
        ),
        "FOUNDRY_MODEL": _output("gpt-4.1-mini"),
        "TOOLBOX_NAME": _output("fibey-toolbox"),
        "TOOLBOX_MCP_URL": _output(
            "https://example.services.ai.azure.com/toolboxes/fibey-toolbox"
        ),
        "searchServiceName": _output("fibey-jf-1026-search"),
        "AZURE_SEARCH_ENDPOINT": _output(
            "https://fibey-jf-1026-search.search.windows.net"
        ),
        "storageAccountName": _output("fibeystorage"),
        "AZURE_OPENAI_ENDPOINT": _output("https://example.openai.azure.com/"),
        "AZURE_SEARCH_INDEX": _output("foundry-iq-docs-index"),
        "AZURE_AI_PROJECT_ACR_CONNECTION_NAME": _output("acr-connection"),
        "registryLoginServer": _output("example.azurecr.io"),
        # This output must never be copied into an azd environment by the resolver.
        "APPLICATIONINSIGHTS_CONNECTION_STRING": _output("private-value"),
    }
    return {
        "id": (
            f"/subscriptions/{subscription}/providers/Microsoft.Resources/"
            f"deployments/{name}"
        ),
        "name": name,
        "properties": {
            "provisioningState": state,
            "timestamp": timestamp,
            "outputs": outputs,
        },
    }


def resolve(deployment: dict[str, Any], **overrides: str) -> dict[str, str]:
    inputs = {
        "deployment_name": "selected-deployment",
        "subscription_id": "sub-123",
        "tenant_id": "tenant-123",
        "resource_group": "rg-fibey-jf-1026",
        "account_name": "ai-account-sidzt6mr5x5qu",
        "project_name": "fibey-project",
        "location": "eastus2",
        "search_location": "centralus",
        "model_version": "2025-04-14",
    }
    inputs.update(overrides)
    return resolver.resolve_context(deployment, **inputs)


class ResolveDeploymentContextTests(unittest.TestCase):
    def test_normalizes_case_and_derives_only_documented_kb_values(self) -> None:
        context = resolve(arm_deployment())
        self.assertEqual(context["AZURE_SEARCH_LOCATION"], "centralus")
        self.assertEqual(context["AZURE_SEARCH_ENDPOINT"], "https://fibey-jf-1026-search.search.windows.net")
        self.assertEqual(context["KB_NAME"], "fibey-field-ops-kb")
        self.assertEqual(context["KB_CONNECTION_NAME"], "kb-fibey-field-ops-kb")
        self.assertEqual(
            context["KB_MCP_URL"],
            "https://fibey-jf-1026-search.search.windows.net/knowledgebases/"
            "fibey-field-ops-kb/mcp?api-version=2026-05-01-preview",
        )
        self.assertEqual(
            context["FOUNDRY_PROJECT_ENDPOINT"],
            context["AZURE_AI_PROJECT_ENDPOINT"],
        )
        self.assertEqual(context["FOUNDRY_MODEL_CAPACITY"], "100")

    def test_accepts_existing_kb_outputs_without_replacing_them(self) -> None:
        deployment = arm_deployment()
        deployment["properties"]["outputs"].update(
            {
                "KB_NAME": _output("fibey-field-ops-kb"),
                "KB_CONNECTION_NAME": _output("kb-fibey-field-ops-kb"),
                "KB_MCP_URL": _output(
                    "https://fibey-jf-1026-search.search.windows.net/knowledgebases/fibey-field-ops-kb/mcp"
                    "?api-version=2026-05-01-preview"
                ),
            }
        )
        context = resolve(deployment)
        self.assertEqual(context["KB_MCP_URL"], deployment["properties"]["outputs"]["KB_MCP_URL"]["value"])

    def test_rejects_conflicting_aliases(self) -> None:
        deployment = arm_deployment()
        deployment["properties"]["outputs"]["FOUNDRY_PROJECT_ENDPOINT"] = _output(
            "https://different.example/"
        )
        with self.assertRaisesRegex(resolver.ContextError, "Conflicting aliases"):
            resolve(deployment)

    def test_rejects_wrong_subscription_and_resource_group(self) -> None:
        with self.assertRaisesRegex(resolver.ContextError, "subscription"):
            resolve(arm_deployment(subscription="other-sub"))
        with self.assertRaisesRegex(resolver.ContextError, "resource group"):
            resolve(arm_deployment(resource_group="other-rg"))

    def test_rejects_failed_or_differently_named_deployment(self) -> None:
        with self.assertRaisesRegex(resolver.ContextError, "has not succeeded"):
            resolve(arm_deployment(state="Failed"))
        with self.assertRaisesRegex(resolver.ContextError, "different deployment"):
            resolve(arm_deployment(), deployment_name="another-deployment")

    def test_selects_latest_matching_successful_deployment(self) -> None:
        older = arm_deployment(
            name="older",
            timestamp="2026-10-27T10:00:00Z",
        )
        newest = arm_deployment(
            name="newest",
            timestamp="2026-10-28T10:00:00Z",
        )
        unrelated = arm_deployment(
            name="other-resource-group",
            resource_group="rg-other",
            timestamp="2026-10-29T10:00:00Z",
        )
        failed = arm_deployment(
            name="failed-latest",
            state="Failed",
            timestamp="2026-10-30T10:00:00Z",
        )
        selected = resolver.select_latest_matching_deployment(
            [older, unrelated, failed, newest],
            subscription_id="sub-123",
            tenant_id="tenant-123",
            resource_group="rg-fibey-jf-1026",
            account_name="ai-account-sidzt6mr5x5qu",
            project_name="fibey-project",
        )
        self.assertEqual(selected["name"], "newest")

    def test_auto_selection_rejects_no_matching_deployment(self) -> None:
        with self.assertRaisesRegex(resolver.ContextError, "No successful"):
            resolver.select_latest_matching_deployment(
                [arm_deployment(resource_group="rg-other")],
                subscription_id="sub-123",
                tenant_id="tenant-123",
                resource_group="rg-fibey-jf-1026",
                account_name="ai-account-sidzt6mr5x5qu",
                project_name="fibey-project",
            )

    def test_auto_selection_rejects_ambiguous_latest_deployments(self) -> None:
        deployments = [
            arm_deployment(name="candidate-a"),
            arm_deployment(name="candidate-b"),
        ]
        with self.assertRaisesRegex(resolver.ContextError, "specify a deployment name"):
            resolver.select_latest_matching_deployment(
                deployments,
                subscription_id="sub-123",
                tenant_id="tenant-123",
                resource_group="rg-fibey-jf-1026",
                account_name="ai-account-sidzt6mr5x5qu",
                project_name="fibey-project",
            )

    def test_rejects_bootstrap_identity_mismatch(self) -> None:
        with self.assertRaisesRegex(resolver.ContextError, "Foundry account"):
            resolve(arm_deployment(), account_name="other-account")

    def test_excludes_non_allowlisted_outputs(self) -> None:
        context = resolve(arm_deployment())
        self.assertNotIn("APPLICATIONINSIGHTS_CONNECTION_STRING", context)
        self.assertNotIn("private-value", context.values())


if __name__ == "__main__":
    unittest.main()
