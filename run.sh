#!/bin/bash

################################################################################
# Universal Run Script
# Automatically detects Python and runs the monitor
################################################################################

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo "Error: Python not found"
    exit 1
fi

# Run monitor with arguments
$PYTHON monitor.py "$@"
