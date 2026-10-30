#!/usr/bin/env bash
set -euo pipefail

workspace="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
parameter_file="$workspace/services/infra/fibey-ui-gateway.parameters.json"
template_file="$workspace/services/infra/fibey-ui-gateway.bicep"
agent_parameter_file="$workspace/agent/parameters.json"
operation="${1:-plan}"

if [[ "$operation" != "plan" && "$operation" != "deploy" ]]; then
  echo "Usage: $0 [plan|deploy] [image-tag]" >&2
  exit 2
fi

for command_name in az jq curl git python3; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Required command is not installed: $command_name" >&2
    exit 1
  fi
done

project_resource_id="$(jq -er '.AZURE_AI_PROJECT_ID' "$agent_parameter_file")"
target_subscription_id="$(cut -d '/' -f 3 <<< "$project_resource_id")"
resource_group="$(cut -d '/' -f 5 <<< "$project_resource_id")"
active_subscription_id="$(az account show --query id --output tsv)"

if [[ "$resource_group" != "rg-fibey-ent" ]]; then
  echo "The checked-in Fibey project targets '$resource_group'; expected the confirmed UI/gateway resource group rg-fibey-ent." >&2
  exit 1
fi

if [[ "$active_subscription_id" != "$target_subscription_id" ]]; then
  echo "Azure CLI subscription does not match the existing Fibey Foundry project; no resources were changed." >&2
  exit 1
fi

image_tag="${2:-${IMAGE_TAG:-$(git -C "$workspace" rev-parse --short=12 HEAD)-$(date -u +%Y%m%d%H%M%S)-$(python3 -c 'import uuid; print(uuid.uuid4().hex[:8])')}}"
if [[ ! "$image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "Image tag must be a valid 1-128 character container tag." >&2
  exit 1
fi

resource_exists() {
  local description="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    echo "Required existing Azure resource not found ($description); no resources were changed." >&2
    return 1
  fi
}

resource_exists "resource group $resource_group" \
  az group show --name "$resource_group" --query id --output tsv
resource_exists "Container Apps environment fibey-ent-services-cae" \
  az containerapp env show --resource-group "$resource_group" \
    --name fibey-ent-services-cae --query id --output tsv
resource_exists "registry acrfibeyentlls4qn3t" \
  az acr show --resource-group "$resource_group" \
    --name acrfibeyentlls4qn3t --query id --output tsv
resource_exists "registry pull identity fibey-ent-services-acr-pull" \
  az identity show --resource-group "$resource_group" \
    --name fibey-ent-services-acr-pull --query id --output tsv
resource_exists "Foundry project $project_resource_id" \
  az resource show --ids "$project_resource_id" --query id --output tsv

preview="$(az deployment group what-if \
  --resource-group "$resource_group" \
  --name "fibey-ui-gateway-preview-$image_tag" \
  --template-file "$template_file" \
  --parameters "@$parameter_file" "imageTag=$image_tag" \
  --result-format ResourceIdOnly \
  --no-pretty-print \
  --only-show-errors --output json)"

unexpected_changes="$(jq -r '
  [.changes[]?
   | select(.changeType != "NoChange" and .changeType != "Ignore")
   | select(
       ((.resourceId // "") | ascii_downcase) as $id
       | (($id | endswith("/providers/microsoft.app/containerapps/fibey-ent-ui"))
          or ($id | endswith("/providers/microsoft.app/containerapps/fibey-ent-gateway"))
          or (($id | contains("/projects/fibey-project/providers/microsoft.authorization/roleassignments/"))))
       | not
     )
   | .resourceId] | .[]
' <<< "$preview")"

if [[ -n "$unexpected_changes" ]]; then
  echo "The deployment preview contains changes outside the approved UI/gateway scope:" >&2
  printf '%s\n' "$unexpected_changes" >&2
  exit 1
fi

printf 'Target subscription: %s\n' "$target_subscription_id"
printf 'Target resource group: %s\n' "$resource_group"
printf 'Unique image tag: %s\n' "$image_tag"
printf 'Deployment preview:\n%s\n' "$preview"

if [[ "$operation" == "plan" ]]; then
  echo "Preview only; no images or Azure resources were changed."
  exit 0
fi

registry_name="$(jq -er '.parameters.registryName.value' "$parameter_file")"
existing_repositories="$(az acr repository list \
  --name "$registry_name" --only-show-errors --output json)"
for repository in fibey-ui fibey-gateway; do
  if jq -e --arg repository "$repository" \
    'any(.[]; . == $repository)' <<< "$existing_repositories" >/dev/null; then
    existing_tags="$(az acr repository show-tags \
      --name "$registry_name" \
      --repository "$repository" \
      --only-show-errors --output json)"
    if jq -e --arg image_tag "$image_tag" \
      'any(.[]; . == $image_tag)' <<< "$existing_tags" >/dev/null; then
      echo "Image tag '$image_tag' already exists in $repository; refusing to overwrite it." >&2
      exit 1
    fi
  fi
done

az acr build \
  --registry "$registry_name" \
  --image "fibey-gateway:$image_tag" \
  --platform linux/amd64 \
  --file "$workspace/Dockerfile" \
  "$workspace" \
  --only-show-errors
az acr build \
  --registry "$registry_name" \
  --image "fibey-ui:$image_tag" \
  --platform linux/amd64 \
  --file "$workspace/ui/Dockerfile" \
  "$workspace/ui" \
  --only-show-errors

deployment_outputs="$(az deployment group create \
  --resource-group "$resource_group" \
  --name "fibey-ui-gateway-$image_tag" \
  --template-file "$template_file" \
  --parameters "@$parameter_file" "imageTag=$image_tag" \
  --mode Incremental \
  --only-show-errors \
  --query properties.outputs --output json)"

ui_url="$(jq -er '.uiUrl.value' <<< "$deployment_outputs")"
gateway_internal_url="$(jq -er '.gatewayInternalUrl.value' <<< "$deployment_outputs")"
if [[ "$gateway_internal_url" != https://* ]]; then
  echo "The gateway ingress is not the expected internal HTTPS endpoint." >&2
  exit 1
fi

check_health() {
  local url="$1"
  local attempt=0
  local response
  while (( attempt < 36 )); do
    attempt=$((attempt + 1))
    if response="$(curl --fail --silent --show-error --max-time 10 "$url" 2>/dev/null)"; then
      if [[ "$url" == */api/health ]]; then
        if jq -e '.status == "healthy" and .mode == "hosted"' <<< "$response" >/dev/null 2>&1; then
          return 0
        fi
      else
        if grep -q '<html' <<< "$response"; then
          return 0
        fi
      fi
    fi
    sleep 10
  done
  echo "Service did not become healthy at $url" >&2
  return 1
}

check_health "${ui_url%/}/"
check_health "${ui_url%/}/api/health"

printf 'Fibey UI URL: %s\n' "$ui_url"
printf 'Fibey gateway internal URL: %s\n' "$gateway_internal_url"
printf 'Gateway health via UI proxy: %s\n' "$(curl --fail --silent --show-error "${ui_url%/}/api/health")"
printf 'Image tag: %s\n' "$image_tag"
