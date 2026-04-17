#!/usr/bin/env bash
# =============================================================================
# Website Monitor — macOS Installer
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "  ${GREEN}[OK]${RESET}  $*"; }
warn() { echo -e "  ${YELLOW}[!!]${RESET}  $*"; }
fail() { echo -e "  ${RED}[XX]${RESET}  $*"; exit 1; }
hdr()  { echo -e "\n  ${CYAN}${BOLD}$*${RESET}"; echo -e "  $(printf '─%.0s' $(seq 1 ${#1}))"; }

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &>/dev/null && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
MONITOR_PY="$PROJECT_DIR/monitor.py"

echo -e "\n  ${CYAN}${BOLD}=========================================${RESET}"
echo -e "  ${CYAN}${BOLD} Website Monitor — macOS Installer      ${RESET}"
echo -e "  ${CYAN}${BOLD}=========================================${RESET}"

# ── macOS version ────────────────────────────────────────────────────────────
SW=$(sw_vers -productVersion 2>/dev/null || echo "unknown")
echo -e "\n  macOS $SW"

# ── Python ───────────────────────────────────────────────────────────────────
hdr "Checking Python"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" --version 2>&1 | awk '{print $2}')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 8 ]; then
            PYTHON="$cmd"
            ok "Found Python $ver ($(command -v $cmd))"
            break
        else
            warn "Found Python $ver — need 3.8+"
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python 3.8+ not found. Install with: brew install python3"
fi

# ── monitor.py ───────────────────────────────────────────────────────────────
hdr "Locating monitor.py"
[ -f "$MONITOR_PY" ] || fail "monitor.py not found at $MONITOR_PY"
ok "Found: $MONITOR_PY"
chmod +x "$MONITOR_PY"
ok "Executable bit set"

# ── Directories ──────────────────────────────────────────────────────────────
hdr "Creating directories"
mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/reports"
ok "logs/ and reports/ ready"

# ── Dotenv ───────────────────────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/.env.example" ] && [ ! -f "$PROJECT_DIR/.env" ]; then
    hdr ".env setup"
    warn "No .env file found — copy .env.example and fill in secrets if needed"
    echo "  cp $PROJECT_DIR/.env.example $PROJECT_DIR/.env"
fi

# ── Test ─────────────────────────────────────────────────────────────────────
hdr "Running test check"
cd "$PROJECT_DIR"
"$PYTHON" monitor.py test || warn "Test exited with errors — check your config"

# ── LaunchAgent ──────────────────────────────────────────────────────────────
hdr "LaunchAgent (auto-start)"
read -r -p "  Create a LaunchAgent to run every 5 minutes? [y/N] " reply
echo
if [[ "${reply,,}" == "y" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.websitemonitor.plist"
    PYTHON_FULL=$(command -v "$PYTHON")

    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.websitemonitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_FULL}</string>
        <string>${MONITOR_PY}</string>
        <string>check</string>
    </array>
    <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
    <key>StartInterval</key>   <integer>300</integer>
    <key>RunAtLoad</key>       <true/>
    <key>StandardOutPath</key> <string>${PROJECT_DIR}/logs/launchd.log</string>
    <key>StandardErrorPath</key><string>${PROJECT_DIR}/logs/launchd-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST

    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load   "$PLIST"
    ok "LaunchAgent loaded from $PLIST"
    echo -e "     Runs every 5 minutes. Manage with:"
    echo -e "       launchctl unload $PLIST     # stop"
    echo -e "       launchctl list | grep website  # status"
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo -e "\n  ${CYAN}${BOLD}=========================================${RESET}"
echo -e "  ${GREEN}${BOLD} Installation complete!${RESET}"
echo -e "  ${CYAN}${BOLD}=========================================${RESET}\n"
echo -e "  Next steps:"
echo -e "  ${YELLOW}1.${RESET} Edit  monitor_config.json  to add your sites"
echo -e "  ${YELLOW}2.${RESET} Run:  $PYTHON monitor.py check"
echo -e "  ${YELLOW}3.${RESET} Or:   ./monitor.py check"
echo
