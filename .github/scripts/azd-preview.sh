#!/usr/bin/env bash
# Run the non-mutating azd what-if preview from the current directory.
set -euo pipefail

log_file="${RUNNER_TEMP:?RUNNER_TEMP is required}/azd-preview.log"
if ! azd provision --preview --no-prompt > "$log_file" 2>&1; then
  echo "azd preview failed; no provisioning was started." >&2
  sed -n '1,240p' "$log_file"
  exit 1
fi
echo "=== Bicep what-if (preview only) ==="
cat "$log_file"
echo "Foundation preview completed. This operation did not provision, deploy, publish, or assign roles." >> "$GITHUB_STEP_SUMMARY"
