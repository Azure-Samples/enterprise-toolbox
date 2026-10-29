from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ServicesInfrastructureTests(unittest.TestCase):
    def test_services_registry_name_matches_foundation(self) -> None:
        foundation = (ROOT / "foundation/infra/resources.bicep").read_text(encoding="utf-8")
        services = (ROOT / "services/infra/main.bicep").read_text(encoding="utf-8")

        self.assertIn(
            "var registryName = 'acr${take(sanitizedEnvironmentName, 15)}${take(resourceToken, 8)}'",
            foundation,
        )
        self.assertIn(
            "var registryName = 'acr${take(sanitizedEnvironmentName, 15)}${take(resourceToken, 8)}'",
            services,
        )
        self.assertIn(
            "uniqueString(subscription().subscriptionId, resourceGroup().id, environmentName)",
            foundation,
        )
        self.assertIn(
            "uniqueString(subscription().subscriptionId, resourceGroup().id, environmentName)",
            services,
        )

    def test_container_apps_use_managed_identity_for_registry_pulls(self) -> None:
        registry = (ROOT / "foundation/infra/modules/container-registry.bicep").read_text(encoding="utf-8")
        container_app = (ROOT / "services/infra/modules/container-app.bicep").read_text(encoding="utf-8")
        services = (ROOT / "services/infra/main.bicep").read_text(encoding="utf-8")

        self.assertIn("adminUserEnabled: false", registry)
        self.assertNotIn("output password", registry)
        script = (ROOT / "scripts/deploy-prerequisites.sh").read_text(encoding="utf-8")
        self.assertIn('az role assignment list --scope "$registry_resource_id" --output json', script)
        self.assertIn("identity: registryIdentityResourceId", container_app)
        self.assertIn("type: 'UserAssigned'", container_app)
        self.assertNotIn("registryPassword", container_app)
        self.assertIn("roleDefinitionId: acrPullRoleDefinitionId", services)
        self.assertIn("minReplicas: 0", services)

    def test_staged_deployment_outputs_service_urls(self) -> None:
        services = (ROOT / "services/infra/main.bicep").read_text(encoding="utf-8")
        script = (ROOT / "scripts/deploy-prerequisites.sh").read_text(encoding="utf-8")

        self.assertIn("param deployApps bool = false", services)
        self.assertIn("output inventoryMcpUrl string", services)
        self.assertIn("output workOrdersApiUrl string", services)
        self.assertIn("az deployment group what-if", script)
        self.assertIn("--result-format ResourceIdOnly", script)
        self.assertIn("--no-pretty-print", script)
        self.assertIn("[.changes[]?", script)
        self.assertIn('"deployApps=false"', script)
        self.assertIn('deployment_parameters[3]="deployApps=true"', script)
        self.assertIn("az acr build", script)
        self.assertIn("--platform linux/amd64", script)
        self.assertIn('"inventory-mcp:$image_tag"', script)
        self.assertIn('"work-orders-api:$image_tag"', script)
        self.assertIn('jq -er \'.inventoryMcpUrl.value\'', script)
        self.assertIn('jq -er \'.workOrdersApiUrl.value\'', script)
        self.assertIn("foundation/parameters.json", script)
        self.assertIn(".INVENTORY_MCP_URL = $inventory_url", script)
        self.assertIn(".WORK_ORDERS_API_URL = $work_orders_url", script)
        self.assertIn('mv "$temporary_parameter_file" "$parameter_file"', script)
        deployment_workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / ".github/workflows").glob("deploy-*.yml")
        )
        self.assertNotIn("deploy-prerequisites.sh", deployment_workflows)


if __name__ == "__main__":
    unittest.main()
