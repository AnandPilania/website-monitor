#!/usr/bin/env bash
# =============================================================================
# Website Monitor — Universal runner (macOS + Linux)
# Usage: ./run.sh [check|monitor|report|test|config|rotate] [OPTIONS]
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &>/dev/null && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
MONITOR_PY="$PROJECT_DIR/monitor.py"

# Load .env if present (simple key=value, no export needed since subprocess inherits env)
if [ -f "$PROJECT_DIR/.env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "$PROJECT_DIR/.env" | grep -v '^\s*$' | xargs)
fi

# Detect Python
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python not found. Install Python 3.8+ first." >&2
    exit 1
fi

if [ ! -f "$MONITOR_PY" ]; then
    echo "Error: monitor.py not found at $MONITOR_PY" >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON" "$MONITOR_PY" "$@"
