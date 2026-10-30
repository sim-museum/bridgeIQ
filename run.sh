#!/bin/bash
# Launch the BridgeIQ AI
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Pidfile dir for background Claude critiques (see bridgeIQ/ui/game_logger.py).
# Polled at script exit so the launcher doesn't move the BDL mid-insert.
CRITIQUE_PID_DIR="/tmp/bridgeIQ_critiques_$$"
export CRITIQUE_PID_DIR
mkdir -p "$CRITIQUE_PID_DIR"

if [[ ! -d venv ]]; then
    echo "First run: creating the Python virtual environment (venv/)..."
    python3 -m venv venv || { echo "Error: python3 -m venv failed (sudo apt install python3-venv)"; exit 1; }
    venv/bin/pip install -q -r requirements.txt || { echo "Error: pip install failed"; exit 1; }
fi

source venv/bin/activate

# --- Self-heal: confirm the system DDS library is reachable ---
# bridgeIQ/backend/dds.py loads libdds.so.0 via ctypes find_library.
# The system libdds0 apt package provides the SONAME directly.
if ! ldconfig -p 2>/dev/null | grep -q 'libdds\.so'; then
    echo "WARNING: libdds not found. Install with: sudo apt install libdds0"
fi

# --- Launch ---

cd bridgeIQ
[[ -n "${SGL_GAME_STARTED_MARKER:-}" ]] && touch "$SGL_GAME_STARTED_MARKER"
# stderr is kept out of the terminal but logged: native-library messages
# (e.g. libdds aborts) never reach the Python log.
STDERR_LOG="$HOME/.local/share/bridgeIQ/logs/stderr_$(date +%Y%m%d).log"
mkdir -p "$(dirname "$STDERR_LOG")"
echo "=== $(date '+%F %T') start pid $$ ===" >> "$STDERR_LOG"
python3 main.py "$@" 2>>"$STDERR_LOG"
exit_code=$?
echo "=== $(date '+%F %T') exit $exit_code ===" >> "$STDERR_LOG"

if [[ $exit_code -ne 0 ]]; then
    echo ""
    echo "bridgeIQ exited with error code $exit_code"
    echo "Re-run with verbose output:"
    echo "  cd $(pwd) && source ../venv/bin/activate && python3 main.py 2>&1 | head -50"
fi

# --- Wait for any in-flight Claude critiques before the launcher moves files ---
cd "$SCRIPT_DIR"
if [[ -d "$CRITIQUE_PID_DIR" ]]; then
    _wait_deadline=$(( $(date +%s) + 180 ))
    while :; do
        _alive=0
        shopt -s nullglob
        for _pidfile in "$CRITIQUE_PID_DIR"/*.pid; do
            _pid=$(cat "$_pidfile" 2>/dev/null) || { rm -f "$_pidfile"; continue; }
            if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then
                _alive=1
            else
                rm -f "$_pidfile"
            fi
        done
        shopt -u nullglob
        (( _alive == 0 )) && break
        (( $(date +%s) >= _wait_deadline )) && { echo "Critique wait timed out; continuing." ; break; }
        echo "Waiting for Claude critique to finish..."
        sleep 2
    done
    rmdir "$CRITIQUE_PID_DIR" 2>/dev/null || true
fi

exit $exit_code
