@echo off
:: ============================================================================
:: Website Monitor — Windows Launcher
:: Double-click this file (or call from CMD) to run a monitoring check.
:: ============================================================================
setlocal enabledelayedexpansion

echo.
echo  ==========================================
echo   Website Monitor for Windows
echo  ==========================================
echo.

:: ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo.
    echo  Download from  https://www.python.org/downloads/
    echo  During install: check "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

:: ── Locate monitor.py ───────────────────────────────────────────────────────
set "SCRIPT=%~dp0..\monitor.py"
if not exist "%SCRIPT%" (
    :: Fallback: same directory as this batch file
    set "SCRIPT=%~dp0monitor.py"
)
if not exist "%SCRIPT%" (
    echo  [ERROR] monitor.py not found.
    echo  Expected location: %SCRIPT%
    echo.
    pause
    exit /b 1
)

:: ── Load optional .env (naive key=value parser) ──────────────────────────────
set "ENVFILE=%~dp0..\.env"
if exist "%ENVFILE%" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ENVFILE%") do (
        set "%%A=%%B"
    )
)

:: ── Run ─────────────────────────────────────────────────────────────────────
echo  Running check...
echo.
python "%SCRIPT%" check
set EXIT_CODE=%errorlevel%

echo.
echo  ==========================================
if %EXIT_CODE% equ 0 (
    echo   All sites healthy.
) else (
    echo   One or more sites require attention!
)
echo  ==========================================
echo.
pause
exit /b %EXIT_CODE%
