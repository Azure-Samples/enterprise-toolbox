#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && ${1:-} != --setup-knowledge-base ) ]]; then
  echo "Usage: $0 [--setup-knowledge-base]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Fibey Agent Setup ==="

# Python setup
echo "Setting up Python environment with uv..."
cd "$PROJECT_ROOT"
uv sync

# Node setup
echo "Setting up UI dependencies..."
cd "$PROJECT_ROOT/ui"
npm install

# .env
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env from .env.example — fill in your values."
fi

if [[ $# -eq 1 ]]; then
  echo "Setting up the knowledge base..."
  (
    cd "$PROJECT_ROOT/foundation"
    bash ./scripts/setup-knowledge-base.sh
  )
fi

echo "=== Setup complete ==="
echo "Run ./scripts/start-dev.sh to start development servers."
