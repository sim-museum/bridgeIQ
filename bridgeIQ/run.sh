#!/bin/bash
# Run BridgeIQ application

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(dirname "$SCRIPT_DIR")"

# Pick the interpreter: prefer the project venv's python directly (robust even
# when a sourced venv doesn't put `python` on PATH in non-interactive shells),
# then fall back to python3 / python.
if [ -x "$BRIDGE_DIR/venv/bin/python" ]; then
    PYBIN="$BRIDGE_DIR/venv/bin/python"
    [ -f "$BRIDGE_DIR/venv/bin/activate" ] && source "$BRIDGE_DIR/venv/bin/activate"
else
    PYBIN="$(command -v python3 || command -v python)"
fi

# Set environment variables
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
export QT_AUTO_SCREEN_SCALE_FACTOR=1

# Run the application
cd "$SCRIPT_DIR"
exec "$PYBIN" main.py "$@"
