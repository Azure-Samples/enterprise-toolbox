#!/usr/bin/env bash
# Initialize desired settings only; no Azure resource or deployment discovery.
set -euo pipefail

context_file="$(mktemp)"
settings_file="$(mktemp)"
trap 'rm -f "$context_file" "$settings_file"' EXIT

subscription_id="${AZURE_SUBSCRIPTION_ID:-}"
if [[ "$OPERATION" != *-validate ]]; then
  subscription_id="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}"
fi

jq -e \
  --arg operation "${OPERATION:?OPERATION is required}" \
  --arg subscription_id "$subscription_id" '
  def clean:
    if type == "string" then (explode | all(. >= 32 and . != 127))
    elif type == "array" then all(.[]; clean)
    elif type == "object" then all(to_entries[]; (.key | clean) and (.value | clean))
    else true end;
  def valid_url:
    type == "string" and startswith("https://")
    and (test("[[:space:]#]") | not)
    and (try (capture("^https://(?<authority>[^/]+)").authority | contains("@") | not) catch false);
  def valid_principal:
    (.AZURE_PRINCIPAL_ID | type == "string" and
      test("^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"));
  . as $parameters |
  if type != "object" then error("parameter file must be a JSON object")
  elif (all(to_entries[]; (.key | test("^[A-Z][A-Z0-9_]*$")) and (.value | clean)) | not)
    then error("parameter names or values contain unsupported characters")
  elif (.AZURE_ENV_NAME | type != "string" or
    (test("^[A-Za-z0-9][A-Za-z0-9-]{0,63}$") | not))
    then error("AZURE_ENV_NAME must be a 1-64 character alphanumeric/hyphen name")
  elif (.AZURE_LOCATION | type != "string" or length == 0)
    then error("AZURE_LOCATION is required")
  elif ($operation == "foundation-preview" and (valid_principal | not))
    then error("AZURE_PRINCIPAL_ID must be the deployment principal object UUID")
  elif ($operation == "foundation-deploy" and
    ((valid_principal | not) or (.INVENTORY_MCP_URL | valid_url | not) or
      (.WORK_ORDERS_API_URL | valid_url | not)))
    then error("foundation deployment requires a principal object UUID and HTTPS service URLs")
  elif ($operation == "agent" and
    (["AZURE_RESOURCE_GROUP", "AZURE_AI_ACCOUNT_ID", "AZURE_AI_PROJECT_ID",
      "AZURE_AI_FOUNDRY_PROJECT_ID", "AZURE_AI_ACCOUNT_NAME", "AZURE_AI_PROJECT_NAME",
      "AZURE_AI_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT",
      "AZURE_AI_PROJECT_ACR_CONNECTION_NAME", "AZURE_CONTAINER_REGISTRY_ENDPOINT",
      "FOUNDRY_MODEL", "TOOLBOX_MCP_URL"] |
    any(. as $key | ($parameters[$key] | type != "string" or length == 0))))
    then error("agent parameters must explicitly provide all target IDs, endpoints, registry, and model values")
  elif ($operation == "agent" and
    (.AZURE_AI_PROJECT_ID != .AZURE_AI_FOUNDRY_PROJECT_ID or
     .AZURE_AI_PROJECT_ENDPOINT != .FOUNDRY_PROJECT_ENDPOINT or
     (.AZURE_AI_ACCOUNT_ID + "/projects/" + .AZURE_AI_PROJECT_NAME) != .AZURE_AI_PROJECT_ID or
     ((.AZURE_AI_PROJECT_ID | startswith("/subscriptions/" + $subscription_id + "/")) | not) or
     (.AZURE_AI_PROJECT_ENDPOINT | valid_url | not) or
     (.FOUNDRY_PROJECT_ENDPOINT | valid_url | not) or
     (.TOOLBOX_MCP_URL | valid_url | not) or
     (.AZURE_CONTAINER_REGISTRY_ENDPOINT |
       test("^[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$") | not) or
     (.AZURE_AI_PROJECT_ENDPOINT |
       endswith("/api/projects/" + $parameters.AZURE_AI_PROJECT_NAME) | not)))
    then error("agent project IDs/endpoints must agree with each other and the authenticated subscription")
  elif ($operation | IN("foundation-preview", "foundation-deploy", "agent",
    "foundation-validate", "agent-validate") | not)
    then error("unsupported deployment operation")
  else . end
' "${PARAMETER_FILE:?PARAMETER_FILE is required}" > "$context_file"

if [[ "$OPERATION" == *-validate ]]; then
  exit 0
fi

jq -er 'to_entries[] | "\(.key)=\(if (.value | type) == "string" then .value else (.value | tojson) end)"' \
  "$context_file" > "$settings_file"
environment_name="$(jq -er '.AZURE_ENV_NAME' "$context_file")"
location="$(jq -er '.AZURE_LOCATION' "$context_file")"
settings=()
while IFS= read -r setting; do
  settings+=("$setting")
done < "$settings_file"

azd env new "$environment_name" --no-prompt \
  --subscription "${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}" \
  --location "$location" \
  --cwd "${PROJECT_DIRECTORY:?PROJECT_DIRECTORY is required}" >/dev/null
azd env set "${settings[@]}" "AZURE_TENANT_ID=${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}" \
  --environment "$environment_name" --cwd "$PROJECT_DIRECTORY" >/dev/null
# Provisioned outputs must not be shadowed by initial, possibly empty settings.
printf 'AZURE_ENV_NAME=%s\n' "$environment_name" >> "${GITHUB_ENV:?GITHUB_ENV is required}"
echo "Initialized desired deployment settings from the parameter file; no deployment outputs were queried." \
  >> "$GITHUB_STEP_SUMMARY"
