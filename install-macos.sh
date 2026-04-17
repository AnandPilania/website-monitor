#!/bin/bash

################################################################################
# Website Monitor - macOS Installer
################################################################################

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Website Monitor - macOS Installer${NC}"
echo -e "${CYAN}=====================================${NC}"
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
    echo -e "${YELLOW}Install with: brew install python3${NC}"
    exit 1
fi

# Check Homebrew
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}⚠ Homebrew not found (optional)${NC}"
    echo "  Install from: https://brew.sh"
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

# Offer to create LaunchAgent
echo ""
read -p "Would you like to create a LaunchAgent to run every 5 minutes? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    PLIST_PATH="$HOME/Library/LaunchAgents/com.websitemonitor.plist"
    SCRIPT_PATH="$(pwd)/monitor.py"
    PYTHON_PATH=$(which $PYTHON_CMD)

    cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.websitemonitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$SCRIPT_PATH</string>
        <string>check</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>$(pwd)/logs/monitor.log</string>
    <key>StandardErrorPath</key>
    <string>$(pwd)/logs/error.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST

    # Load the LaunchAgent
    launchctl unload "$PLIST_PATH" 2>/dev/null
    launchctl load "$PLIST_PATH"

    echo -e "${GREEN}✓ LaunchAgent created and loaded${NC}"
    echo -e "${CYAN}  Monitor will run every 5 minutes${NC}"
    echo -e "${CYAN}  To stop: launchctl unload $PLIST_PATH${NC}"
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
