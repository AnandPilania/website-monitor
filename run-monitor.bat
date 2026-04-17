@echo off
REM Website Monitor - Windows Launcher
REM Double-click to run monitoring checks

echo ====================================
echo Website Monitor for Windows
echo ====================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

REM Check if monitor.py exists
if not exist "monitor.py" (
    echo ERROR: monitor.py not found in current directory
    echo Please ensure monitor.py is in the same folder as this script
    pause
    exit /b 1
)

REM Run the monitor
echo Running website monitor...
echo.
python monitor.py check

echo.
echo ====================================
echo Check complete! Press any key to exit
pause >nul
