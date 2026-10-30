#!/usr/bin/env bash
set -euo pipefail

workspace="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
parameter_file="${PARAMETER_FILE:-$workspace/foundation/parameters.json}"
template_file="$workspace/services/infra/main.bicep"
subscription_id="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}"

for command_name in az jq curl git; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is not installed: $command_name" >&2
    exit 1
  fi
done

if [[ ! -f "$parameter_file" ]]; then
  echo "Parameter file does not exist: $parameter_file" >&2
  exit 1
fi

active_subscription_id="$(az account show --query id --output tsv)"
if [[ "$active_subscription_id" != "$subscription_id" ]]; then
  echo "Azure CLI subscription does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

environment_name="$(jq -er '.AZURE_ENV_NAME | select(type == "string" and length > 0)' "$parameter_file")"
location="$(jq -er '.AZURE_LOCATION | select(type == "string" and length > 0)' "$parameter_file")"
resource_group="${AZURE_RESOURCE_GROUP:-$(jq -r '.AZURE_RESOURCE_GROUP // empty' "$parameter_file")}"
resource_group="${resource_group:-rg-$environment_name}"
image_tag="${IMAGE_TAG:-$(git -C "$workspace" rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)}"

if [[ ! "$image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "IMAGE_TAG must be a valid 1-128 character container tag." >&2
  exit 1
fi

if az group show --name "$resource_group" --query name --output tsv >/dev/null 2>&1; then
  existing_location="$(az group show --name "$resource_group" --query location --output tsv)"
  normalized_existing_location="$(printf '%s' "$existing_location" | tr '[:upper:]' '[:lower:]')"
  normalized_location="$(printf '%s' "$location" | tr '[:upper:]' '[:lower:]')"
  if [[ "$normalized_existing_location" != "$normalized_location" ]]; then
    echo "Resource group $resource_group is in $existing_location, not $location." >&2
    exit 1
  fi
else
  az group create --name "$resource_group" --location "$location" \
    --only-show-errors --output none
fi

deployment_parameters=(
  "environmentName=$environment_name"
  "location=$location"
  "imageTag=$image_tag"
  "deployApps=false"
)

preview="$(az deployment group what-if \
  --resource-group "$resource_group" \
  --name "fibey-services-preview-$image_tag" \
  --template-file "$template_file" \
  --parameters "${deployment_parameters[@]}" \
  --result-format ResourceIdOnly \
  --no-pretty-print \
  --only-show-errors --output json)"

registry_resource_id="$(jq -er '
  [.changes[]?
   | select((.resourceId // "" | ascii_downcase)
      | test("/providers/microsoft\\.containerregistry/registries/[^/]+$"))
   | .resourceId][0]
  | select(type == "string" and length > 0)
' <<< "$preview")"
registry_name="${registry_resource_id##*/}"

if ! az acr show --name "$registry_name" --resource-group "$resource_group" \
  --query id --output tsv >/dev/null 2>&1; then
  name_available="$(az acr check-name --name "$registry_name" \
    --query nameAvailable --output tsv)"
  if [[ "$name_available" != "true" ]]; then
    echo "The foundation-compatible ACR name '$registry_name' is unavailable outside $resource_group; no registry was created." >&2
    exit 1
  fi
fi

base_outputs="$(az deployment group create \
  --resource-group "$resource_group" \
  --name "fibey-services-base-$image_tag" \
  --template-file "$template_file" \
  --parameters "${deployment_parameters[@]}" \
  --mode Incremental \
  --only-show-errors \
  --query properties.outputs --output json)"
registry_name="$(jq -er '.registryName.value' <<< "$base_outputs")"
registry_resource_id="$(jq -er '.registryResourceId.value' <<< "$base_outputs")"

principal_type="$(az account show --query user.type --output tsv)"
case "$principal_type" in
  user)
    principal_id="$(az ad signed-in-user show --query id --output tsv)"
    role_principal_type="User"
    ;;
  servicePrincipal)
    client_id="$(az account show --query user.name --output tsv)"
    principal_id="$(az ad sp show --id "$client_id" --query id --output tsv)"
    role_principal_type="ServicePrincipal"
    ;;
  *)
    echo "Unsupported Azure CLI identity type for ACR build submission: $principal_type" >&2
    exit 1
    ;;
esac

has_acr_push="$(az role assignment list --scope "$registry_resource_id" --output json |
  jq --arg principal_id "$principal_id" '
    any(.[]; (.principalId | ascii_downcase) == ($principal_id | ascii_downcase)
      and .roleDefinitionName == "AcrPush")
  ')"
if [[ "$has_acr_push" != "true" ]]; then
  az role assignment create \
    --assignee-object-id "$principal_id" \
    --assignee-principal-type "$role_principal_type" \
    --role AcrPush \
    --scope "$registry_resource_id" \
    --only-show-errors --output none
fi

az acr build \
  --registry "$registry_name" \
  --image "inventory-mcp:$image_tag" \
  --platform linux/amd64 \
  --file "$workspace/services/inventory-mcp/Dockerfile" \
  "$workspace/services/inventory-mcp" \
  --only-show-errors
az acr build \
  --registry "$registry_name" \
  --image "work-orders-api:$image_tag" \
  --platform linux/amd64 \
  --file "$workspace/services/work-orders-api/Dockerfile" \
  "$workspace/services/work-orders-api" \
  --only-show-errors

deployment_parameters[3]="deployApps=true"
app_outputs="$(az deployment group create \
  --resource-group "$resource_group" \
  --name "fibey-services-apps-$image_tag" \
  --template-file "$template_file" \
  --parameters "${deployment_parameters[@]}" \
  --mode Incremental \
  --only-show-errors \
  --query properties.outputs --output json)"

inventory_url="$(jq -er '.inventoryMcpUrl.value' <<< "$app_outputs")"
work_orders_url="$(jq -er '.workOrdersApiUrl.value' <<< "$app_outputs")"
inventory_health_url="${inventory_url%%/mcp*}/health"
work_orders_health_url="${work_orders_url%/}/health"

check_health() {
  local url="$1"
  local attempt=0
  while (( attempt < 30 )); do
    attempt=$((attempt + 1))
    if curl --fail --silent --show-error --max-time 10 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 10
  done
  echo "Service did not become healthy at $url" >&2
  return 1
}

check_health "$inventory_health_url"
check_health "$work_orders_health_url"

parameter_file_mode="$(stat -c '%a' "$parameter_file" 2>/dev/null || stat -f '%Lp' "$parameter_file")"
temporary_parameter_file="$(mktemp "${parameter_file}.tmp.XXXXXX")"
cleanup() {
  if [[ -n "$temporary_parameter_file" ]]; then
    rm -f -- "$temporary_parameter_file"
  fi
}
trap cleanup EXIT

if ! jq \
  --arg inventory_url "$inventory_url" \
  --arg work_orders_url "$work_orders_url" \
  '.INVENTORY_MCP_URL = $inventory_url | .WORK_ORDERS_API_URL = $work_orders_url' \
  "$parameter_file" > "$temporary_parameter_file"; then
  echo "Failed to update service URLs in $parameter_file." >&2
  exit 1
fi

chmod "$parameter_file_mode" "$temporary_parameter_file"
mv "$temporary_parameter_file" "$parameter_file"
temporary_parameter_file=""

printf 'Inventory MCP URL: %s\n' "$inventory_url"
printf 'Work Orders API URL: %s\n' "$work_orders_url"
printf 'Updated parameters: %s\n' "$parameter_file"
printf 'Image tag: %s\n' "$image_tag"
