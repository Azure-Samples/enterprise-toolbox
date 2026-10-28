#!/usr/bin/env python3
"""Safely normalize allowlisted outputs from a selected subscription ARM deployment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ALIASES: dict[str, tuple[str, ...]] = {
    "tenant_id": ("AZURE_TENANT_ID", "azureTenantId"),
    "resource_group": (
        "AZURE_RESOURCE_GROUP",
        "azureResourceGroup",
        "RESOURCE_GROUP",
        "resourceGroupName",
        "resourceGroup",
    ),
    "account_id": (
        "AZURE_AI_ACCOUNT_ID",
        "AZURE_AI_FOUNDRY_ACCOUNT_ID",
        "azureAiAccountId",
        "azureAiFoundryAccountId",
    ),
    "project_id": (
        "AZURE_AI_PROJECT_ID",
        "AZURE_AI_FOUNDRY_PROJECT_ID",
        "azureAiProjectId",
        "azureAiFoundryProjectId",
    ),
    "account_name": ("AZURE_AI_ACCOUNT_NAME", "azureAiAccountName"),
    "project_name": ("AZURE_AI_PROJECT_NAME", "azureAiProjectName"),
    "project_endpoint": (
        "AZURE_AI_PROJECT_ENDPOINT",
        "FOUNDRY_PROJECT_ENDPOINT",
        "azureAiProjectEndpoint",
        "foundryProjectEndpoint",
    ),
    "openai_endpoint": ("AZURE_OPENAI_ENDPOINT", "azureOpenAiEndpoint"),
    "model": ("FOUNDRY_MODEL", "foundryModel"),
    "toolbox_name": ("TOOLBOX_NAME", "toolboxName"),
    "toolbox_mcp_url": ("TOOLBOX_MCP_URL", "toolboxMcpUrl"),
    "kb_name": ("KB_NAME", "kbName"),
    "kb_mcp_url": ("KB_MCP_URL", "kbMcpUrl"),
    "kb_connection_name": ("KB_CONNECTION_NAME", "kbConnectionName"),
    "search_endpoint": (
        "AZURE_SEARCH_ENDPOINT",
        "SEARCH_SERVICE_ENDPOINT",
        "searchServiceEndpoint",
        "azureSearchEndpoint",
    ),
    "search_index": ("AZURE_SEARCH_INDEX", "azureSearchIndex", "searchIndexName"),
    "search_service_name": ("searchServiceName",),
    "storage_account_name": ("storageAccountName",),
    "acr_connection_name": (
        "AZURE_AI_PROJECT_ACR_CONNECTION_NAME",
        "azureAiProjectAcrConnectionName",
        "acrConnectionName",
    ),
    "registry_endpoint": (
        "AZURE_CONTAINER_REGISTRY_ENDPOINT",
        "REGISTRY_LOGIN_SERVER",
        "registryLoginServer",
        "azureContainerRegistryEndpoint",
    ),
}


class ContextError(ValueError):
    """Raised when selected deployment context cannot be trusted."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContextError(f"Duplicate JSON key {key!r} in the selected deployment.")
        result[key] = value
    return result


def _output_value(outputs: dict[str, Any], aliases: tuple[str, ...]) -> str | None:
    aliases_lower = {alias.lower() for alias in aliases}
    found: list[tuple[str, str]] = []
    for key, entry in outputs.items():
        if key.lower() not in aliases_lower:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
            raise ContextError(f"Deployment output {key!r} has an invalid value.")
        value = entry["value"].strip()
        if any(character in value for character in ("\x00", "\r", "\n", "\t")):
            raise ContextError(f"Deployment output {key!r} contains unsupported control characters.")
        found.append((key, value))
    if not found:
        return None
    distinct = {value for _, value in found}
    if len(distinct) != 1:
        names = ", ".join(name for name, _ in found)
        raise ContextError(f"Conflicting aliases for deployment output: {names}.")
    value = found[0][1]
    if not value:
        raise ContextError(f"Deployment output {found[0][0]!r} is empty.")
    return value


def _require(value: str | None, label: str) -> str:
    if value is None:
        raise ContextError(f"Selected deployment is missing required output {label}.")
    return value


def _deployment_subscription(deployment: dict[str, Any]) -> str:
    deployment_id = deployment.get("id")
    if not isinstance(deployment_id, str):
        raise ContextError("Selected deployment has no ARM resource ID.")
    match = re.search(r"/subscriptions/([^/]+)/", deployment_id, re.IGNORECASE)
    if not match:
        raise ContextError("Selected deployment ARM ID has no subscription.")
    return match.group(1)


def _output_matches(outputs: dict[str, Any], aliases: tuple[str, ...], expected: str) -> bool:
    aliases_lower = {alias.lower() for alias in aliases}
    values: set[str] = set()
    for key, entry in outputs.items():
        if key.lower() not in aliases_lower:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("value"), str):
            return False
        value = entry["value"].strip()
        if not value or any(character in value for character in ("\x00", "\r", "\n", "\t")):
            return False
        values.add(value.casefold())
    return values == {expected.casefold()}


def select_latest_matching_deployment(
    deployments: Any,
    *,
    subscription_id: str,
    tenant_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
) -> dict[str, Any]:
    if not isinstance(deployments, list):
        raise ContextError("ARM deployment discovery did not return a JSON array.")

    matches: list[tuple[datetime, dict[str, Any]]] = []
    for deployment in deployments:
        if not isinstance(deployment, dict):
            continue
        properties = deployment.get("properties")
        deployment_name = deployment.get("name")
        deployment_id = deployment.get("id")
        if (
            not isinstance(properties, dict)
            or not isinstance(properties.get("provisioningState"), str)
            or properties["provisioningState"].casefold() != "succeeded"
            or not isinstance(deployment_name, str)
            or not isinstance(deployment_id, str)
            or deployment_id.rstrip("/").rsplit("/", 1)[-1].casefold()
            != deployment_name.casefold()
        ):
            continue
        try:
            actual_subscription = _deployment_subscription(deployment)
        except ContextError:
            continue
        if actual_subscription.casefold() != subscription_id.casefold():
            continue

        outputs = properties.get("outputs")
        if not isinstance(outputs, dict) or not all(
            _output_matches(outputs, ALIASES[key], expected)
            for key, expected in (
                ("tenant_id", tenant_id),
                ("resource_group", resource_group),
                ("account_name", account_name),
                ("project_name", project_name),
            )
        ):
            continue

        timestamp = properties.get("timestamp")
        if not isinstance(timestamp, str):
            raise ContextError(
                f"Matching ARM deployment {deployment_name!r} has no timestamp; "
                "specify its name explicitly."
            )
        try:
            deployment_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ContextError(
                f"Matching ARM deployment {deployment_name!r} has an invalid timestamp; "
                "specify its name explicitly."
            ) from error
        if deployment_time.tzinfo is None:
            raise ContextError(
                f"Matching ARM deployment {deployment_name!r} has no timezone in its "
                "timestamp; specify its name explicitly."
            )
        matches.append((deployment_time, deployment))

    if not matches:
        raise ContextError(
            "No successful subscription-scope ARM deployment matched the configured "
            "subscription, tenant, resource group, Foundry account, and project. "
            "Specify a deployment name explicitly or provision the foundation first."
        )

    latest_time = max(deployment_time for deployment_time, _ in matches)
    latest = [
        deployment for deployment_time, deployment in matches
        if deployment_time == latest_time
    ]
    if len(latest) != 1:
        names = ", ".join(sorted(deployment["name"] for deployment in latest))
        raise ContextError(
            f"Multiple matching ARM deployments share the latest timestamp ({names}); "
            "specify a deployment name explicitly."
        )
    return latest[0]


def _resource_names(resource_id: str) -> tuple[str | None, str | None]:
    segments = resource_id.strip("/").split("/")
    names = {segments[index].lower(): segments[index + 1] for index in range(len(segments) - 1)}
    return names.get("accounts"), names.get("projects")


def resolve_context(
    deployment: dict[str, Any],
    *,
    subscription_id: str,
    tenant_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
    deployment_name: str,
    location: str,
    search_location: str,
    model_version: str,
    inventory_mcp_url: str = "",
    work_orders_api_url: str = "",
    kb_name: str = "fibey-field-ops-kb",
    foundry_model_capacity: str = "100",
    principal_id: str = "",
    principal_type: str = "User",
) -> dict[str, str]:
    if not isinstance(deployment, dict):
        raise ContextError("Selected ARM deployment must be a JSON object.")
    properties = deployment.get("properties")
    if (
        not isinstance(properties, dict)
        or not isinstance(properties.get("provisioningState"), str)
        or properties["provisioningState"].casefold() != "succeeded"
    ):
        raise ContextError("The selected ARM deployment has not succeeded.")
    if not deployment_name or not isinstance(deployment.get("name"), str) or (
        deployment["name"].lower() != deployment_name.lower()
    ):
        raise ContextError("ARM returned a different deployment than the selected deployment.")
    deployment_id = deployment.get("id")
    if (
        not isinstance(deployment_id, str)
        or deployment_id.rstrip("/").rsplit("/", 1)[-1].casefold() != deployment_name.casefold()
    ):
        raise ContextError("ARM deployment resource ID does not match the selected deployment name.")
    actual_subscription = _deployment_subscription(deployment)
    if actual_subscription.lower() != subscription_id.lower():
        raise ContextError("Selected deployment subscription does not match the bootstrap subscription.")
    outputs = properties.get("outputs")
    if not isinstance(outputs, dict):
        raise ContextError("Selected deployment has no outputs object.")

    values = {name: _output_value(outputs, aliases) for name, aliases in ALIASES.items()}
    if _require(values["tenant_id"], "AZURE_TENANT_ID").lower() != tenant_id.lower():
        raise ContextError("Selected deployment tenant does not match the bootstrap tenant.")
    if _require(values["resource_group"], "AZURE_RESOURCE_GROUP").lower() != resource_group.lower():
        raise ContextError("Selected deployment resource group does not match the bootstrap resource group.")
    if _require(values["account_name"], "AZURE_AI_ACCOUNT_NAME").lower() != account_name.lower():
        raise ContextError("Selected deployment Foundry account does not match the bootstrap account.")
    if _require(values["project_name"], "AZURE_AI_PROJECT_NAME").lower() != project_name.lower():
        raise ContextError("Selected deployment Foundry project does not match the bootstrap project.")
    if (
        resource_group != "rg-fibey-jf-1026"
        or account_name != "ai-account-sidzt6mr5x5qu"
        or project_name != "fibey-project"
    ):
        raise ContextError("Bootstrap target does not match the approved Fibey foundation.")

    account_id = _require(values["account_id"], "AZURE_AI_ACCOUNT_ID")
    project_id = _require(values["project_id"], "AZURE_AI_PROJECT_ID")
    account_from_id, _ = _resource_names(account_id)
    project_account, project_from_id = _resource_names(project_id)
    if account_from_id is None or account_from_id.lower() != account_name.lower():
        raise ContextError("Foundry account resource ID does not match the bootstrap account.")
    if project_from_id is None or project_from_id.lower() != project_name.lower():
        raise ContextError("Foundry project resource ID does not match the bootstrap project.")
    if project_account is None or project_account.lower() != account_name.lower():
        raise ContextError("Foundry project resource ID belongs to a different account.")
    for resource_id in (account_id, project_id):
        id_subscription = re.search(r"/subscriptions/([^/]+)/", resource_id, re.IGNORECASE)
        id_group = re.search(r"/resourceGroups/([^/]+)/", resource_id, re.IGNORECASE)
        if (
            not id_subscription
            or id_subscription.group(1).lower() != subscription_id.lower()
            or not id_group
            or id_group.group(1).lower() != resource_group.lower()
        ):
            raise ContextError("Foundry resource ID does not match the bootstrap subscription/resource group.")

    search_endpoint = values["search_endpoint"]
    search_service_name = values["search_service_name"]
    resolved_kb_name = values["kb_name"] or kb_name
    if resolved_kb_name.lower() != kb_name.lower():
        raise ContextError("Selected deployment knowledge base does not match the configured KB name.")
    expected_kb_connection = f"kb-{resolved_kb_name}"
    kb_connection = values["kb_connection_name"] or expected_kb_connection
    if kb_connection.lower() != expected_kb_connection.lower():
        raise ContextError("Selected deployment knowledge base connection name is unexpected.")
    search_endpoint = _require(search_endpoint, "AZURE_SEARCH_ENDPOINT")
    search_service_name = _require(search_service_name, "searchServiceName")
    expected_search_endpoint = f"https://{search_service_name}.search.windows.net"
    if search_endpoint.rstrip("/").casefold() != expected_search_endpoint.casefold():
        raise ContextError("Selected Search service name and endpoint do not match.")
    expected_kb_url = (
        f"{search_endpoint.rstrip('/')}/knowledgebases/{resolved_kb_name}"
        "/mcp?api-version=2026-05-01-preview"
    )
    if values["kb_mcp_url"] and values["kb_mcp_url"].rstrip("/") != expected_kb_url.rstrip("/"):
        raise ContextError("Selected deployment knowledge base endpoint is inconsistent.")
    values["kb_mcp_url"] = values["kb_mcp_url"] or expected_kb_url
    model = _require(values["model"], "FOUNDRY_MODEL")
    if model != "gpt-4.1-mini":
        raise ContextError("Selected deployment model does not match the approved model.")
    if location != "eastus2" or search_location != "centralus":
        raise ContextError("Bootstrap regions do not match the approved eastus2/centralus anchors.")
    if model_version != "2025-04-14" or foundry_model_capacity != "100":
        raise ContextError("Model version/capacity do not match the approved deployment anchors.")
    bootstrap_values = (
        subscription_id,
        tenant_id,
        resource_group,
        account_name,
        project_name,
        location,
        search_location,
        model_version,
        foundry_model_capacity,
        principal_id,
        principal_type,
        inventory_mcp_url,
        work_orders_api_url,
    )
    if any(any(character in value for character in ("\x00", "\r", "\n", "\t")) for value in bootstrap_values):
        raise ContextError("Bootstrap values must not contain control characters.")

    context = {
        "AZURE_SUBSCRIPTION_ID": subscription_id,
        "AZURE_TENANT_ID": tenant_id,
        "AZURE_RESOURCE_GROUP": resource_group,
        "AZURE_LOCATION": location,
        "AZURE_SEARCH_LOCATION": search_location,
        "AZURE_AI_ACCOUNT_ID": account_id,
        "AZURE_AI_PROJECT_ID": project_id,
        "AZURE_AI_FOUNDRY_PROJECT_ID": project_id,
        "AZURE_AI_ACCOUNT_NAME": account_name,
        "AZURE_AI_PROJECT_NAME": project_name,
        "AZURE_AI_PROJECT_ENDPOINT": _require(values["project_endpoint"], "AZURE_AI_PROJECT_ENDPOINT"),
        "FOUNDRY_PROJECT_ENDPOINT": _require(values["project_endpoint"], "AZURE_AI_PROJECT_ENDPOINT"),
        "FOUNDRY_MODEL": model,
        "FOUNDRY_MODEL_VERSION": model_version,
        "FOUNDRY_MODEL_CAPACITY": foundry_model_capacity,
        "AZURE_PRINCIPAL_TYPE": principal_type,
        "TOOLBOX_NAME": values["toolbox_name"] or "fibey-toolbox",
        "TOOLBOX_MCP_URL": _require(values["toolbox_mcp_url"], "TOOLBOX_MCP_URL"),
        "KB_NAME": resolved_kb_name,
        "KB_MCP_URL": _require(values["kb_mcp_url"], "KB_MCP_URL"),
        "KB_CONNECTION_NAME": kb_connection,
        "AZURE_SEARCH_ENDPOINT": _require(search_endpoint, "AZURE_SEARCH_ENDPOINT"),
        "searchServiceName": _require(search_service_name, "searchServiceName"),
        "AZURE_SEARCH_INDEX": _require(values["search_index"], "AZURE_SEARCH_INDEX"),
        "storageAccountName": _require(values["storage_account_name"], "storageAccountName"),
        "AZURE_OPENAI_ENDPOINT": _require(values["openai_endpoint"], "AZURE_OPENAI_ENDPOINT"),
        "AZURE_AI_PROJECT_ACR_CONNECTION_NAME": _require(
            values["acr_connection_name"], "AZURE_AI_PROJECT_ACR_CONNECTION_NAME"
        ),
        "AZURE_CONTAINER_REGISTRY_ENDPOINT": _require(values["registry_endpoint"], "AZURE_CONTAINER_REGISTRY_ENDPOINT"),
        "AZURE_AI_DEPLOYMENTS_LOCATION": location,
        "SEARCH_SERVICE_LOCATION": search_location,
    }
    for key, value in {
        "AZURE_PRINCIPAL_ID": principal_id,
        "INVENTORY_MCP_URL": inventory_mcp_url,
        "WORK_ORDERS_API_URL": work_orders_api_url,
    }.items():
        if value:
            context[key] = value
    return context


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="ARM deployment JSON file")
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--account-name", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--deployment-name", default="")
    parser.add_argument("--kb-name", default="fibey-field-ops-kb")
    parser.add_argument("--location", required=True)
    parser.add_argument("--search-location", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--foundry-model-capacity", default="100")
    parser.add_argument("--principal-id", default="")
    parser.add_argument("--principal-type", default="User")
    parser.add_argument("--inventory-mcp-url", default="")
    parser.add_argument("--work-orders-api-url", default="")
    args = parser.parse_args()
    try:
        deployment_data = json.loads(
            Path(args.input).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
        )
        deployment_name = args.deployment_name
        if not deployment_name:
            deployment_data = select_latest_matching_deployment(
                deployment_data,
                subscription_id=args.subscription_id,
                tenant_id=args.tenant_id,
                resource_group=args.resource_group,
                account_name=args.account_name,
                project_name=args.project_name,
            )
            deployment_name = deployment_data["name"]
        deployment = deployment_data
        context = resolve_context(
            deployment,
            subscription_id=args.subscription_id,
            tenant_id=args.tenant_id,
            resource_group=args.resource_group,
            account_name=args.account_name,
            project_name=args.project_name,
            kb_name=args.kb_name,
            deployment_name=deployment_name,
            location=args.location,
            search_location=args.search_location,
            model_version=args.model_version,
            foundry_model_capacity=args.foundry_model_capacity,
            principal_id=args.principal_id,
            principal_type=args.principal_type,
            inventory_mcp_url=args.inventory_mcp_url,
            work_orders_api_url=args.work_orders_api_url,
        )
    except (OSError, json.JSONDecodeError, ContextError) as error:
        print(f"Foundation context validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(context, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
