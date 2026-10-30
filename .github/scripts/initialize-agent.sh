#!/usr/bin/env bash
# Validate agent settings and optionally initialize a runner-local azd environment.
set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && "${1:-}" != --validate ) ]]; then
  echo "Usage: initialize-agent.sh [--validate]" >&2
  exit 2
fi

context_file="$(mktemp)"
settings_file="$(mktemp)"
trap 'rm -f "$context_file" "$settings_file"' EXIT

subscription_id=""
if [[ "${1:-}" != --validate ]]; then
  subscription_id="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}"
fi

jq -e \
  --arg subscription_id "$subscription_id" '
  def clean:
    if type == "string" then (explode | all(. >= 32 and . != 127))
    elif type == "array" then all(.[]; clean)
    elif type == "object" then all(to_entries[]; (.key | clean) and (.value | clean))
    else true end;
  def valid_url:
    type == "string" and startswith("https://")
    and (test("[[:space:]#]") | not)
    and ((try (capture("^https://(?<authority>[^/]+)").authority | contains("@") | not) catch false) // false);
  def parse_project_id:
    (try capture("^/subscriptions/(?<subscription>[^/]+)/resourceGroups/(?<resource_group>[^/]+)/providers/Microsoft[.]CognitiveServices/accounts/(?<account_name>[^/]+)/projects/(?<project_name>[^/]+)$")
    catch null) // null;
  if type != "object" then error("parameter file must be a JSON object")
  elif (all(to_entries[]; (.key | test("^[A-Z][A-Z0-9_]*$")) and (.value | clean)) | not)
    then error("parameter names or values contain unsupported characters")
  elif (.AZURE_ENV_NAME | type != "string" or
    (test("^[A-Za-z0-9][A-Za-z0-9-]{0,63}$") | not))
    then error("AZURE_ENV_NAME must be a 1-64 character alphanumeric/hyphen name")
  elif (.AZURE_LOCATION | type != "string" or length == 0)
    then error("AZURE_LOCATION is required")
  else
    . as $agent |
    ($agent.AZURE_AI_PROJECT_ID | parse_project_id) as $project |
    if (["AZURE_AI_PROJECT_ID", "AZURE_AI_PROJECT_ENDPOINT",
         "AZURE_AI_PROJECT_ACR_CONNECTION_NAME", "AZURE_CONTAINER_REGISTRY_ENDPOINT",
         "FOUNDRY_MODEL", "TOOLBOX_MCP_URL"] |
        any(. as $key | ($agent[$key] | type != "string" or length == 0)))
      then error("agent parameters require project ID and endpoint, registry connection and endpoint, model, and toolbox URL")
    elif $project == null
      then error("AZURE_AI_PROJECT_ID must be a Foundry project resource ID")
    elif ($subscription_id != "" and
      ($project.subscription | ascii_downcase) != ($subscription_id | ascii_downcase))
      then error("AZURE_AI_PROJECT_ID must belong to the authenticated subscription")
    elif (($agent.AZURE_AI_PROJECT_ENDPOINT | valid_url | not) or
      ($agent.TOOLBOX_MCP_URL | valid_url | not) or
      ($agent.AZURE_CONTAINER_REGISTRY_ENDPOINT |
        test("^[A-Za-z0-9]+([.-][A-Za-z0-9]+)*$") | not))
      then error("agent endpoints must use HTTPS and the registry value must be a hostname")
    elif ($agent.AZURE_AI_PROJECT_ENDPOINT |
      endswith("/api/projects/" + $project.project_name) | not)
      then error("AZURE_AI_PROJECT_ENDPOINT must identify the project in AZURE_AI_PROJECT_ID")
    else
      $agent + {
        AZURE_RESOURCE_GROUP: $project.resource_group,
        AZURE_AI_ACCOUNT_ID: ($agent.AZURE_AI_PROJECT_ID | sub("/projects/[^/]+$"; "")),
        AZURE_AI_FOUNDRY_PROJECT_ID: $agent.AZURE_AI_PROJECT_ID,
        AZURE_AI_ACCOUNT_NAME: $project.account_name,
        AZURE_AI_PROJECT_NAME: $project.project_name,
        FOUNDRY_PROJECT_ENDPOINT: $agent.AZURE_AI_PROJECT_ENDPOINT
      }
    end
  end
' "${PARAMETER_FILE:?PARAMETER_FILE is required}" > "$context_file"

if [[ "${1:-}" == --validate ]]; then
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
printf 'AZURE_ENV_NAME=%s\n' "$environment_name" >> "${GITHUB_ENV:?GITHUB_ENV is required}"
echo "Initialized agent settings from its parameters. No deployment outputs were queried." \
  >> "$GITHUB_STEP_SUMMARY"
