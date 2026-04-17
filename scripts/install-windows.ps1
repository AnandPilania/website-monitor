#Requires -Version 5.1
<#
.SYNOPSIS
    Website Monitor — Windows Installer

.DESCRIPTION
    Checks Python, creates directory structure, optionally sets up
    a Windows Task Scheduler entry to run every N minutes.

.EXAMPLE
    .\install-windows.ps1
    .\install-windows.ps1 -Interval 10 -NoScheduler
#>

[CmdletBinding()]
param (
    [int]    $Interval   = 5,        # Task Scheduler repeat interval (minutes)
    [switch] $NoScheduler            # Skip Task Scheduler setup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ("  " + ("─" * ($Text.Length))) -ForegroundColor DarkGray
}

function Write-OK([string]$Text)   { Write-Host "  [OK] $Text" -ForegroundColor Green  }
function Write-Warn([string]$Text) { Write-Host "  [!!] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "  [XX] $Text" -ForegroundColor Red    }

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$MonitorPy  = Join-Path $ProjectDir "monitor.py"

Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host "   Website Monitor — Windows Installer"      -ForegroundColor Cyan
Write-Host "  ==========================================" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# Python check
# ---------------------------------------------------------------------------
Write-Header "Checking Python"

$PyExe = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python (\d+\.\d+)") {
            $major = [int]$Matches[1].Split(".")[0]
            $minor = [int]$Matches[1].Split(".")[1]
            if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 8)) {
                Write-Warn "Found $ver — Python 3.8+ required."
                continue
            }
            $PyExe = (Get-Command $candidate).Source
            Write-OK "Found $ver → $PyExe"
            break
        }
    } catch { }
}

if (-not $PyExe) {
    Write-Fail "Python 3.8+ not found."
    Write-Host ""
    Write-Host "  Install from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  Tick 'Add Python to PATH' during installation."   -ForegroundColor Yellow
    exit 1
}

# ---------------------------------------------------------------------------
# monitor.py exists?
# ---------------------------------------------------------------------------
Write-Header "Locating monitor.py"
if (-not (Test-Path $MonitorPy)) {
    Write-Fail "monitor.py not found at $MonitorPy"
    exit 1
}
Write-OK "Found: $MonitorPy"

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
Write-Header "Creating directories"
foreach ($dir in @("logs", "reports")) {
    $full = Join-Path $ProjectDir $dir
    New-Item -ItemType Directory -Force -Path $full | Out-Null
    Write-OK "$dir/"
}

# ---------------------------------------------------------------------------
# Optional: win10toast
# ---------------------------------------------------------------------------
Write-Header "Optional: desktop notifications"
try {
    & $PyExe -m pip install win10toast --quiet --disable-pip-version-check
    Write-OK "win10toast installed"
} catch {
    Write-Warn "win10toast install failed (desktop alerts will use PowerShell fallback)"
}

# ---------------------------------------------------------------------------
# Test run
# ---------------------------------------------------------------------------
Write-Header "Running test check"
Push-Location $ProjectDir
& $PyExe $MonitorPy test
$testExit = $LASTEXITCODE
Pop-Location
if ($testExit -eq 0) { Write-OK "Test passed" } else { Write-Warn "Test returned exit code $testExit" }

# ---------------------------------------------------------------------------
# Task Scheduler
# ---------------------------------------------------------------------------
if (-not $NoScheduler) {
    Write-Header "Task Scheduler"
    $answer = Read-Host "  Create a scheduled task to run every $Interval minutes? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[Yy]") {
        try {
            $action  = New-ScheduledTaskAction `
                -Execute $PyExe `
                -Argument "`"$MonitorPy`" check" `
                -WorkingDirectory $ProjectDir

            $trigger = New-ScheduledTaskTrigger `
                -Once `
                -At (Get-Date) `
                -RepetitionInterval  (New-TimeSpan -Minutes $Interval) `
                -RepetitionDuration  ([TimeSpan]::MaxValue)

            $settings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable

            $principal = New-ScheduledTaskPrincipal `
                -UserId $env:USERNAME `
                -LogonType Interactive

            Register-ScheduledTask `
                -TaskName "WebsiteMonitor" `
                -Action $action `
                -Trigger $trigger `
                -Settings $settings `
                -Principal $principal `
                -Force | Out-Null

            Write-OK "Task 'WebsiteMonitor' created (every $Interval min)"
            Write-Host "     Manage via: taskschd.msc  or  schtasks /Query /TN WebsiteMonitor" `
                -ForegroundColor DarkGray
        } catch {
            Write-Warn "Scheduler setup failed: $_"
            Write-Host "     Try running this script as Administrator." -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host "   Installation complete!" -ForegroundColor Green
Write-Host "  ==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. Edit  monitor_config.json  to add your websites"
Write-Host "    2. Run:  python monitor.py check"
Write-Host "    3. Or double-click:  scripts\run-monitor.bat"
Write-Host ""
