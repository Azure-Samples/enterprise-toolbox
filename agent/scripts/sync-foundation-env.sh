#!/usr/bin/env bash
# Copy the foundation outputs the agent needs into the agent project's azd
# environment (same environment name). In CI, set these as variables instead.
#
# Usage (from agent/): ./scripts/sync-foundation-env.sh <env-name>
set -euo pipefail

ENV_NAME="${1:-${AZURE_ENV_NAME:-}}"
if [ -z "$ENV_NAME" ]; then
  echo "Usage: $0 <azd-env-name>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FOUNDATION_DIR="$SCRIPT_DIR/../../foundation"

KEYS=(
  AZURE_TENANT_ID
  AZURE_SUBSCRIPTION_ID
  AZURE_LOCATION
  AZURE_RESOURCE_GROUP
  AZURE_AI_ACCOUNT_NAME
  AZURE_AI_PROJECT_NAME
  AZURE_AI_PROJECT_ID
  AZURE_AI_PROJECT_ENDPOINT
  FOUNDRY_PROJECT_ENDPOINT
  AZURE_AI_PROJECT_ACR_CONNECTION_NAME
  AZURE_CONTAINER_REGISTRY_ENDPOINT
  APPLICATIONINSIGHTS_CONNECTION_STRING
  FOUNDRY_MODEL
  TOOLBOX_MCP_URL
)

azd env select "$ENV_NAME" --cwd "$SCRIPT_DIR/.." >/dev/null 2>&1 || azd env new "$ENV_NAME" --cwd "$SCRIPT_DIR/.."

for key in "${KEYS[@]}"; do
  value="$(azd env get-value "$key" --cwd "$FOUNDATION_DIR" --environment "$ENV_NAME" 2>/dev/null || true)"
  if [ -z "$value" ]; then
    echo "  skipped $key (not set in foundation env)"
    continue
  fi
  azd env set "$key" "$value" --cwd "$SCRIPT_DIR/.." --environment "$ENV_NAME"
done
echo "✓ Synced foundation values into agent env '$ENV_NAME'."
