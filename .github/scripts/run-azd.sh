#!/usr/bin/env bash
# Run one azd lifecycle command from PROJECT_DIRECTORY.
# Inputs (environment): PROJECT_DIRECTORY, COMMAND (deploy|provision),
# SERVICE (required for deploy), VERIFY_PROMOTION (true|false, deploy only).
set -euo pipefail

cd "${PROJECT_DIRECTORY:?PROJECT_DIRECTORY is required}"
command_name="${COMMAND:?COMMAND is required}"
verify="${VERIFY_PROMOTION:-false}"
log_dir="${RUNNER_TEMP:?RUNNER_TEMP is required}"

case "$command_name" in
  provision)
    if [ "$verify" = "true" ]; then
      echo "verify-promotion applies only to deploy." >&2
      exit 2
    fi
    if ! azd provision --no-prompt > "$log_dir/azd-provision.log" 2>&1; then
      echo "Provision failed. Detailed deployment output was suppressed to avoid leaking values." >&2
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
    if [ "$verify" = "true" ]; then
      promotion_line="$(grep -F "Promoted $service version " "$deploy_log" | tail -n 1 || true)"
      if [ -z "$promotion_line" ]; then
        echo "$service deployment succeeded without reporting a verified promoted default." >&2
        exit 1
      fi
      {
        echo "### Verified $service default"
        echo
        echo "$promotion_line"
      } >> "$GITHUB_STEP_SUMMARY"
    fi
    ;;
  *)
    echo "Unknown azd command: $command_name" >&2
    exit 2
    ;;
esac
