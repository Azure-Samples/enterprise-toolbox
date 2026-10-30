#!/usr/bin/env bash
# Run one azd lifecycle command from PROJECT_DIRECTORY.
# Inputs (environment): PROJECT_DIRECTORY, COMMAND (deploy|provision),
# SERVICE (required for deploy).
set -euo pipefail

cd "${PROJECT_DIRECTORY:?PROJECT_DIRECTORY is required}"
command_name="${COMMAND:?COMMAND is required}"
log_dir="${RUNNER_TEMP:?RUNNER_TEMP is required}"

case "$command_name" in
  provision)
    # Render console diagnostics, not structured provisioning outputs containing values.
    if azd provision --no-prompt --output json 2>&1 |
      tee "$log_dir/azd-provision.jsonl" |
      jq --unbuffered -r '
        select(.type == "consoleMessage") | .data.message |
        if type == "string" then . else error("Invalid azd console message") end
      '; then
      :
    else
      pipeline_status=("${PIPESTATUS[@]}")
      if [ "${pipeline_status[2]}" -ne 0 ]; then
        echo "Could not decode azd JSON diagnostics; raw output values were not printed." >&2
      fi
      if [ "${pipeline_status[1]}" -ne 0 ]; then
        echo "Could not write the runner-local provisioning log." >&2
      fi
      if [ "${pipeline_status[0]}" -ne 0 ]; then
        echo "Provision failed (azd exit code ${pipeline_status[0]}). See provisioning and hook diagnostics above." >&2
      fi
      exit 1
    fi
    ;;
  deploy)
    service="${SERVICE:?SERVICE is required for deploy}"
    deploy_log="$log_dir/deploy-$service.log"
    if ! azd deploy "$service" --no-prompt 2>&1 | tee "$deploy_log"; then
      echo "Deployment of $service failed." >&2
      exit 1
    fi
    ;;
  *)
    echo "Unknown azd command: $command_name" >&2
    exit 2
    ;;
esac
