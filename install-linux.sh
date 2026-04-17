#!/bin/bash

################################################################################
# Website Monitor - Linux Installer
################################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Website Monitor - Linux Installer${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

# Detect OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$NAME
    OS_VERSION=$VERSION_ID
else
    OS="Unknown"
fi

echo "Detected: $OS"
echo ""

# Check Python
echo -e "${YELLOW}Checking Python installation...${NC}"
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo -e "${GREEN}✓ Found: $PYTHON_VERSION${NC}"
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version)
    echo -e "${GREEN}✓ Found: $PYTHON_VERSION${NC}"
    PYTHON_CMD="python"
else
    echo -e "${RED}✗ Python not found!${NC}"
    echo -e "${YELLOW}Installing Python...${NC}"

    if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip
    elif [[ "$OS" == *"CentOS"* ]] || [[ "$OS" == *"Red Hat"* ]]; then
        sudo yum install -y python3 python3-pip
    elif [[ "$OS" == *"Fedora"* ]]; then
        sudo dnf install -y python3 python3-pip
    elif [[ "$OS" == *"Arch"* ]]; then
        sudo pacman -S python python-pip
    else
        echo "Please install Python manually for your distribution"
        exit 1
    fi

    PYTHON_CMD="python3"
fi

# Install optional dependencies
echo ""
echo -e "${YELLOW}Installing optional dependencies...${NC}"

# Install notify-send for desktop notifications
if ! command -v notify-send &> /dev/null; then
    if [[ "$OS" == *"Ubuntu"* ]] || [[ "$OS" == *"Debian"* ]]; then
        sudo apt-get install -y libnotify-bin
    elif [[ "$OS" == *"Fedora"* ]]; then
        sudo dnf install -y libnotify
    fi
fi

if command -v notify-send &> /dev/null; then
    echo -e "${GREEN}✓ Desktop notifications available${NC}"
fi

# Create directories
echo ""
echo -e "${YELLOW}Creating directory structure...${NC}"
mkdir -p logs reports
echo -e "${GREEN}✓ Directories created${NC}"

# Make script executable
echo ""
echo -e "${YELLOW}Making script executable...${NC}"
chmod +x monitor.py
echo -e "${GREEN}✓ monitor.py is now executable${NC}"

# Test the monitor
echo ""
echo -e "${YELLOW}Testing monitor...${NC}"
$PYTHON_CMD monitor.py test

# Offer to create cron job
echo ""
read -p "Would you like to create a cron job to run every 5 minutes? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    SCRIPT_PATH="$(pwd)/monitor.py"
    PYTHON_PATH=$(which $PYTHON_CMD)

    # Add to crontab
    (crontab -l 2>/dev/null; echo "*/5 * * * * cd $(pwd) && $PYTHON_PATH $SCRIPT_PATH check >> logs/cron.log 2>&1") | crontab -

    echo -e "${GREEN}✓ Cron job created${NC}"
    echo -e "${CYAN}  Monitor will run every 5 minutes${NC}"
    echo -e "${CYAN}  View with: crontab -l${NC}"
    echo -e "${CYAN}  Remove with: crontab -e${NC}"
fi

# Offer to create systemd service
echo ""
read -p "Would you like to create a systemd service (requires sudo)? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    SERVICE_FILE="/etc/systemd/system/website-monitor.service"
    SCRIPT_PATH="$(pwd)/monitor.py"
    PYTHON_PATH=$(which $PYTHON_CMD)
    CURRENT_USER=$(whoami)

    sudo tee "$SERVICE_FILE" > /dev/null << SERVICE
[Unit]
Description=Website Monitor Service
After=network.target

[Service]
Type=oneshot
User=$CURRENT_USER
WorkingDirectory=$(pwd)
ExecStart=$PYTHON_PATH $SCRIPT_PATH check

[Install]
WantedBy=multi-user.target
SERVICE

    # Create timer
    TIMER_FILE="/etc/systemd/system/website-monitor.timer"
    sudo tee "$TIMER_FILE" > /dev/null << TIMER
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
    sudo systemctl enable website-monitor.timer
    sudo systemctl start website-monitor.timer

    echo -e "${GREEN}✓ Systemd service created and started${NC}"
    echo -e "${CYAN}  Status: sudo systemctl status website-monitor.timer${NC}"
    echo -e "${CYAN}  Stop: sudo systemctl stop website-monitor.timer${NC}"
fi

echo ""
echo -e "${CYAN}=====================================${NC}"
echo -e "${GREEN}Installation complete!${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Edit monitor_config.json to add your websites"
echo "2. Run: ./monitor.py check"
echo "3. Or run: $PYTHON_CMD monitor.py check"
echo ""
