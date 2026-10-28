#!/usr/bin/env bash
# Validate the GitHub Environment bootstrap values for a foundation context.
# Inputs (environment): AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_RESOURCE_GROUP,
# AZURE_AI_ACCOUNT_NAME, AZURE_AI_PROJECT_NAME, AZURE_PRIMARY_LOCATION,
# AZURE_SEARCH_LOCATION, FOUNDRY_MODEL_VERSION, FOUNDRY_MODEL_CAPACITY,
# and, when REQUIRE_TOOLBOX_ENDPOINTS=true, INVENTORY_MCP_URL and WORK_ORDERS_API_URL.
set -euo pipefail

missing=()
[ -n "${AZURE_SUBSCRIPTION_ID:-}" ] || missing+=(AZURE_SUBSCRIPTION_ID)
[ -n "${AZURE_TENANT_ID:-}" ] || missing+=(AZURE_TENANT_ID)
[ -n "${AZURE_RESOURCE_GROUP:-}" ] || missing+=(AZURE_RESOURCE_GROUP)
[ -n "${AZURE_AI_ACCOUNT_NAME:-}" ] || missing+=(AZURE_AI_ACCOUNT_NAME)
[ -n "${AZURE_AI_PROJECT_NAME:-}" ] || missing+=(AZURE_AI_PROJECT_NAME)
[ -n "${AZURE_PRIMARY_LOCATION:-}" ] || missing+=(AZURE_PRIMARY_LOCATION)
[ -n "${AZURE_SEARCH_LOCATION:-}" ] || missing+=(AZURE_SEARCH_LOCATION)
[ -n "${FOUNDRY_MODEL_VERSION:-}" ] || missing+=(FOUNDRY_MODEL_VERSION)
[ -n "${FOUNDRY_MODEL_CAPACITY:-}" ] || missing+=(FOUNDRY_MODEL_CAPACITY)
if [[ "${REQUIRE_TOOLBOX_ENDPOINTS:-false}" == "true" ]]; then
  [ -n "${INVENTORY_MCP_URL:-}" ] || missing+=(INVENTORY_MCP_URL)
  [ -n "${WORK_ORDERS_API_URL:-}" ] || missing+=(WORK_ORDERS_API_URL)
fi
{
  echo "### Foundation context configuration"
  echo
  echo "Required GitHub Environment secrets: \`AZURE_CLIENT_ID\`, \`AZURE_TENANT_ID\`, \`AZURE_SUBSCRIPTION_ID\`."
  echo "Required GitHub Environment variables: \`AZURE_RESOURCE_GROUP\`, \`AZURE_AI_ACCOUNT_NAME\`, \`AZURE_AI_PROJECT_NAME\`, \`AZURE_PRIMARY_LOCATION\`, \`AZURE_SEARCH_LOCATION\`, \`FOUNDRY_MODEL_VERSION\`, \`FOUNDRY_MODEL_CAPACITY\`."
  echo "Foundation toolbox deploys also require GitHub Environment variables \`INVENTORY_MCP_URL\` and \`WORK_ORDERS_API_URL\`."
  echo "Foundation preview and provision preflight require \`AZURE_PRINCIPAL_ID\` and optionally \`AZURE_PRINCIPAL_TYPE\` to model Bicep role assignments."
  echo "Approved anchors: primary region \`eastus2\`, Search region \`centralus\`, model \`gpt-4.1-mini\` version \`2025-04-14\` at capacity \`100\`."
  echo "An explicitly selected ARM deployment must be successful and belong to this subscription/resource group/account/project. If no name is supplied, the latest matching successful deployment is discovered."
} >> "$GITHUB_STEP_SUMMARY"
if (( ${#missing[@]} )); then
  printf 'Missing required GitHub Environment bootstrap value(s): %s\n' \
    "$(IFS=', '; echo "${missing[*]}")" >&2
  exit 1
fi
