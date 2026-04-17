# Website Monitor - Windows Installer
# Run as Administrator for best results

Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Website Monitor - Windows Installer" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""

# Check Python installation
Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "✓ Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "✗ Python not found!" -ForegroundColor Red
    Write-Host "Please install Python from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Make sure to check 'Add Python to PATH' during installation" -ForegroundColor Yellow
    exit 1
}

# Install optional dependencies
Write-Host ""
Write-Host "Installing optional dependencies..." -ForegroundColor Yellow
pip install win10toast --quiet
if ($?) {
    Write-Host "✓ Desktop notifications enabled" -ForegroundColor Green
} else {
    Write-Host "⚠ Desktop notifications installation failed (optional)" -ForegroundColor Yellow
}

# Create directories
Write-Host ""
Write-Host "Creating directory structure..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
New-Item -ItemType Directory -Force -Path "reports" | Out-Null
Write-Host "✓ Directories created" -ForegroundColor Green

# Test the monitor
Write-Host ""
Write-Host "Testing monitor..." -ForegroundColor Yellow
python monitor.py test

# Create scheduled task
Write-Host ""
$createTask = Read-Host "Would you like to create a scheduled task to run every 5 minutes? (Y/N)"
if ($createTask -eq 'Y' -or $createTask -eq 'y') {
    $scriptPath = (Get-Location).Path + "\monitor.py"
    $pythonPath = (Get-Command python).Source

    $action = New-ScheduledTaskAction -Execute $pythonPath -Argument "check" -WorkingDirectory (Get-Location).Path
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::MaxValue)
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    try {
        Register-ScheduledTask -TaskName "WebsiteMonitor" -Action $action -Trigger $trigger -Settings $settings -Force
        Write-Host "✓ Scheduled task created successfully!" -ForegroundColor Green
        Write-Host "  Task will run every 5 minutes" -ForegroundColor Cyan
    } catch {
        Write-Host "✗ Failed to create scheduled task" -ForegroundColor Red
        Write-Host "  You may need to run this script as Administrator" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "1. Edit monitor_config.json to add your websites" -ForegroundColor White
Write-Host "2. Run: python monitor.py check" -ForegroundColor White
Write-Host "3. Or double-click run-monitor.bat" -ForegroundColor White
Write-Host ""
