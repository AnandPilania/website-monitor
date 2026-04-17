#!/usr/bin/env bash
# =============================================================================
# Website Monitor — Linux Installer
# Supports: Ubuntu/Debian, Fedora/RHEL/CentOS, Arch Linux, openSUSE
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
echo -e "  ${CYAN}${BOLD} Website Monitor — Linux Installer      ${RESET}"
echo -e "  ${CYAN}${BOLD}=========================================${RESET}"

# ── Detect distro ────────────────────────────────────────────────────────────
OS_ID=""
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
fi
echo -e "\n  Distro: ${OS_ID:-unknown}"

# ── Package manager helper ────────────────────────────────────────────────────
install_pkg() {
    case "$OS_ID" in
        ubuntu|debian|linuxmint|pop)
            sudo apt-get install -y "$@" ;;
        fedora)
            sudo dnf install -y "$@" ;;
        centos|rhel|rocky|almalinux)
            sudo yum install -y "$@" ;;
        arch|manjaro)
            sudo pacman -S --noconfirm "$@" ;;
        opensuse*|sles)
            sudo zypper install -y "$@" ;;
        *)
            warn "Unknown distro — please install $* manually"; return 1 ;;
    esac
}

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
    warn "Python 3.8+ not found — attempting install…"
    case "$OS_ID" in
        ubuntu|debian|linuxmint|pop) install_pkg python3 python3-pip ;;
        fedora)                       install_pkg python3 python3-pip ;;
        centos|rhel|rocky|almalinux) install_pkg python3 python3-pip ;;
        arch|manjaro)                 install_pkg python python-pip   ;;
        opensuse*|sles)               install_pkg python3 python3-pip ;;
        *) fail "Cannot auto-install Python on $OS_ID" ;;
    esac
    PYTHON="python3"
    ok "Python installed"
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

# ── Desktop notifications ─────────────────────────────────────────────────────
hdr "Desktop notifications"
if command -v notify-send &>/dev/null; then
    ok "notify-send already available"
else
    if install_pkg libnotify-bin 2>/dev/null || install_pkg libnotify 2>/dev/null; then
        ok "libnotify installed"
    else
        warn "Could not install libnotify — desktop alerts disabled"
    fi
fi

# ── Dotenv ───────────────────────────────────────────────────────────────────
if [ -f "$PROJECT_DIR/.env.example" ] && [ ! -f "$PROJECT_DIR/.env" ]; then
    hdr ".env"
    warn "No .env found — copy .env.example and fill secrets if using email/Slack"
    echo "  cp $PROJECT_DIR/.env.example $PROJECT_DIR/.env"
fi

# ── Test ─────────────────────────────────────────────────────────────────────
hdr "Running test check"
cd "$PROJECT_DIR"
"$PYTHON" monitor.py test || warn "Test exited with errors — review configuration"

# ── Cron ─────────────────────────────────────────────────────────────────────
hdr "Cron job"
read -r -p "  Create a cron job to run every 5 minutes? [y/N] " reply
echo
if [[ "${reply,,}" == "y" ]]; then
    PYTHON_FULL=$(command -v "$PYTHON")
    CRON_LINE="*/5 * * * * cd $PROJECT_DIR && $PYTHON_FULL $MONITOR_PY check >> $PROJECT_DIR/logs/cron.log 2>&1"
    # Append only if not already present
    if crontab -l 2>/dev/null | grep -qF "$MONITOR_PY"; then
        warn "Cron entry already exists — skipping"
    else
        ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
        ok "Cron job added"
        echo -e "     View with:   crontab -l"
        echo -e "     Edit with:   crontab -e"
    fi
fi

# ── systemd ──────────────────────────────────────────────────────────────────
if command -v systemctl &>/dev/null; then
    hdr "systemd timer (optional)"
    read -r -p "  Create a systemd timer to run every 5 minutes? (requires sudo) [y/N] " reply
    echo
    if [[ "${reply,,}" == "y" ]]; then
        PYTHON_FULL=$(command -v "$PYTHON")
        CURRENT_USER=$(whoami)

        sudo tee /etc/systemd/system/website-monitor.service > /dev/null <<SERVICE
[Unit]
Description=Website Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON_FULL} ${MONITOR_PY} check
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

        sudo tee /etc/systemd/system/website-monitor.timer > /dev/null <<TIMER
[Unit]
Description=Website Monitor Timer
Requires=website-monitor.service

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
Unit=website-monitor.service

[Install]
WantedBy=timers.target
TIMER

        sudo systemctl daemon-reload
        sudo systemctl enable --now website-monitor.timer
        ok "systemd timer enabled and started"
        echo -e "     Status: sudo systemctl status website-monitor.timer"
        echo -e "     Logs:   sudo journalctl -u website-monitor.service -f"
        echo -e "     Stop:   sudo systemctl disable --now website-monitor.timer"
    fi
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
