#!/usr/bin/env bash
# Validate one successful ARM deployment and initialize a fresh azd environment.
# Inputs (environment): PROJECT_DIRECTORY, RESOLVER_PATH, DEPLOYMENT_NAME,
# AZURE_ENVIRONMENT_NAME, KB_NAME, and the bootstrap values checked by validate-bootstrap.sh.
set -euo pipefail

deployment_file="$(mktemp)"
context_file="$(mktemp)"
settings_file="$(mktemp)"
trap 'rm -f "$deployment_file" "$context_file" "$settings_file"' EXIT

if [ -n "$DEPLOYMENT_NAME" ]; then
  az deployment sub show \
    --name "$DEPLOYMENT_NAME" \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --output json > "$deployment_file"
else
  az deployment sub list \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --output json > "$deployment_file"
fi
python3 "$RESOLVER_PATH" \
  --input "$deployment_file" \
  --subscription-id "$AZURE_SUBSCRIPTION_ID" \
  --tenant-id "$AZURE_TENANT_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --account-name "$AZURE_AI_ACCOUNT_NAME" \
  --project-name "$AZURE_AI_PROJECT_NAME" \
  --kb-name "$KB_NAME" \
  --deployment-name "$DEPLOYMENT_NAME" \
  --location "$AZURE_PRIMARY_LOCATION" \
  --search-location "$AZURE_SEARCH_LOCATION" \
  --model-version "$FOUNDRY_MODEL_VERSION" \
  --foundry-model-capacity "$FOUNDRY_MODEL_CAPACITY" \
  --principal-id "$AZURE_PRINCIPAL_ID" \
  --principal-type "$AZURE_PRINCIPAL_TYPE" \
  --inventory-mcp-url "$INVENTORY_MCP_URL" \
  --work-orders-api-url "$WORK_ORDERS_API_URL" > "$context_file"

jq -er 'to_entries[] | "\(.key)=\(.value)"' "$context_file" > "$settings_file"
# mapfile is unavailable in macOS bash 3.2
settings=()
while IFS= read -r setting; do
  settings+=("$setting")
done < "$settings_file"

azd env new "$AZURE_ENVIRONMENT_NAME" --no-prompt \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --location "$AZURE_PRIMARY_LOCATION" \
  --cwd "$PROJECT_DIRECTORY" >/dev/null
azd env set "${settings[@]}" \
  --environment "$AZURE_ENVIRONMENT_NAME" \
  --cwd "$PROJECT_DIRECTORY" >/dev/null

{
  echo
  echo "Validated ARM deployment context and initialized a fresh azd environment."
  echo "No deployment outputs or secret values are included in this summary."
} >> "$GITHUB_STEP_SUMMARY"
