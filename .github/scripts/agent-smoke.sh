#!/usr/bin/env bash
# Verify the deployed hosted agent and run a read-only inventory smoke test.
# Run from the agent azd project directory.
set -euo pipefail

azd ai agent show fibey-agent --no-prompt --output json
azd ai agent invoke \
  --new-session \
  "Look up part FIB-002 and report its description and current stock by warehouse. Do not create or update work orders." \
  --no-prompt
