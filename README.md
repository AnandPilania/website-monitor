# 🌍 Cross-Platform Website Monitor

> **Universal Python solution for Windows, macOS, and Linux/Unix**

A production-ready website monitoring system that works seamlessly across all major operating systems. Built with pure Python (no OS-specific dependencies required).

## ✨ Features

### Core Capabilities

- ✅ **True Cross-Platform** - One codebase, works everywhere
- ✅ **HTTP/HTTPS Monitoring** - Full protocol support
- ✅ **Response Time Tracking** - Measure performance
- ✅ **Smart Retry Logic** - Handle transient failures
- ✅ **Comprehensive Logging** - JSON Lines format
- ✅ **Uptime Reports** - Historical analysis
- ✅ **Multiple Alert Channels** - Desktop, Email, Webhook, Slack

### Platform Support

| Platform  | Status            | Versions          |
| --------- | ----------------- | ----------------- |
| 🪟 Windows | ✅ Fully Supported | 7, 8, 10, 11      |
| 🍎 macOS   | ✅ Fully Supported | 10.12+            |
| 🐧 Linux   | ✅ Fully Supported | All major distros |
| 🔧 Unix    | ✅ Fully Supported | FreeBSD, OpenBSD  |

## 🚀 Quick Start

### 1. Install Python (if needed)

**Windows:**

```powershell
# Download from python.org and install
# Ensure "Add Python to PATH" is checked
```

**macOS:**

```bash
brew install python3
```

**Linux:**

```bash
# Ubuntu/Debian
sudo apt-get install python3 python3-pip

# Fedora
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python
```

### 2. Download & Setup

```bash
# Create directory
mkdir website-monitor
cd website-monitor

# Download monitor.py (save the Python script as monitor.py)

# Make executable (Unix/Mac only)
chmod +x monitor.py

# First run - creates config
python monitor.py test
```

### 3. Configure

Edit `monitor_config.json`:

```json
{
    "websites": ["https://www.yoursite.com", "https://api.yourapp.com/health"],
    "timeout": 10,
    "max_response_time": 3000,
    "check_interval": 300
}
```

### 4. Run

```bash
# Single check
python monitor.py check

# Continuous monitoring
python monitor.py monitor

# Generate report
python monitor.py report
```

## 📖 Platform-Specific Guides

### 🪟 Windows Setup

#### Method 1: Easy Setup (Batch File)

1. **Download** `scripts/run-monitor.bat` and `monitor.py`
2. **Double-click** `scripts/run-monitor.bat`
3. Done! ✨

#### Method 2: PowerShell Installation

```powershell
# Run as Administrator
.\scripts\install-windows.ps1
```

This will:

- ✅ Check Python installation
- ✅ Install desktop notifications
- ✅ Create directory structure
- ✅ Optionally set up Task Scheduler

#### Scheduling with Task Scheduler

**GUI Method:**

1. Open Task Scheduler (`Win + R` → `taskschd.msc`)
2. Create Basic Task
3. Name: "Website Monitor"
4. Trigger: Daily, repeat every 5 minutes
5. Action: Start Program
    - Program: `python` (or `C:\Python39\python.exe`)
    - Arguments: `C:\path\to\monitor.py check`
    - Start in: `C:\path\to\`

**PowerShell Method:**

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "C:\path\to\monitor.py check" `
    -WorkingDirectory "C:\path\to\"

$trigger = New-ScheduledTaskTrigger `
    -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

Register-ScheduledTask `
    -TaskName "WebsiteMonitor" `
    -Action $action `
    -Trigger $trigger
```

#### Windows Desktop Notifications

```powershell
# Install optional package
pip install win10toast
```

### 🍎 macOS Setup

#### Quick Install

```bash
# Run installer
chmod +x scripts/install-macos.sh
./scripts/install-macos.sh
```

#### Manual Setup

```bash
# Make executable
chmod +x monitor.py

# Test
./monitor.py test

# Run check
./monitor.py check
```

#### Scheduling with launchd

Create `~/Library/LaunchAgents/com.websitemonitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.websitemonitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/yourname/website-monitor/monitor.py</string>
        <string>check</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.websitemonitor.plist
```

Manage:

```bash
# Check status
launchctl list | grep websitemonitor

# Stop
launchctl unload ~/Library/LaunchAgents/com.websitemonitor.plist

# View logs
tail -f ~/website-monitor/logs/monitor_*.log
```

### 🐧 Linux Setup

#### Quick Install

```bash
# Run installer
chmod +x scripts/install-linux.sh
./scripts/install-linux.sh
```

#### Manual Setup

```bash
# Make executable
chmod +x monitor.py

# Test
./monitor.py test

# Run
./monitor.py check
```

#### Scheduling with Cron

```bash
# Edit crontab
crontab -e

# Add these lines:

# Check every 5 minutes
*/5 * * * * cd /home/user/website-monitor && /usr/bin/python3 monitor.py check >> logs/cron.log 2>&1

# Daily report at 9 AM
0 9 * * * cd /home/user/website-monitor && /usr/bin/python3 monitor.py report --hours 24 | mail -s "Daily Monitor Report" admin@example.com

# Weekly report every Monday
0 10 * * 1 cd /home/user/website-monitor && /usr/bin/python3 monitor.py report --hours 168 > reports/weekly-$(date +\%Y-\%m-\%d).txt
```

#### Scheduling with systemd

Create `/etc/systemd/system/website-monitor.service`:

```ini
[Unit]
Description=Website Monitor
After=network.target

[Service]
Type=oneshot
User=youruser
WorkingDirectory=/home/youruser/website-monitor
ExecStart=/usr/bin/python3 monitor.py check

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/website-monitor.timer`:

```ini
[Unit]
Description=Run Website Monitor

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable website-monitor.timer
sudo systemctl start website-monitor.timer

# Check status
sudo systemctl status website-monitor.timer
sudo systemctl list-timers

# View logs
sudo journalctl -u website-monitor.service -f
```

#### Desktop Notifications (Linux)

```bash
# Ubuntu/Debian
sudo apt-get install libnotify-bin

# Fedora
sudo dnf install libnotify

# Test
notify-send "Test" "Desktop notifications work!"
```

## 🎮 Usage Guide

### Command Reference

```bash
# Basic commands
python monitor.py check              # Single check
python monitor.py monitor            # Continuous mode
python monitor.py report             # Last 24h report
python monitor.py test               # Test config
python monitor.py config             # Show config

# Advanced usage
python monitor.py report --hours 168 # Weekly report
python monitor.py report --hours 720 # Monthly report

# Using config file
python monitor.py check --config /path/to/config.json
```

### Platform-Specific Commands

**Windows (PowerShell):**

```powershell
python monitor.py check
```

**Windows (CMD):**

```cmd
python monitor.py check
```

**macOS/Linux:**

```bash
python3 monitor.py check
# or if executable:
./monitor.py check
```

**Universal (with helper script):**

```bash
./scripts/run.sh check
```

## ⚙️ Configuration

### Complete Configuration Example

```json
{
    "websites": ["https://www.example.com", "https://api.example.com/health", "https://www.google.com"],
    "timeout": 10,
    "max_response_time": 5000,
    "check_interval": 300,
    "max_retries": 3,
    "retry_delay": 5,
    "alerts": {
        "email": {
            "enabled": true,
            "to": "admin@example.com",
            "from": "monitor@example.com",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "your-email@gmail.com",
            "smtp_password": "your-app-password"
        },
        "webhook": {
            "enabled": true,
            "url": "https://your-webhook.com/endpoint"
        },
        "slack": {
            "enabled": true,
            "webhook_url": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
        },
        "desktop": {
            "enabled": true
        }
    }
}
```

### Configuration Options

| Option              | Type  | Default | Description                     |
| ------------------- | ----- | ------- | ------------------------------- |
| `websites`          | array | `[]`    | URLs to monitor                 |
| `timeout`           | int   | `10`    | Connection timeout (seconds)    |
| `max_response_time` | int   | `5000`  | Alert if slower (milliseconds)  |
| `check_interval`    | int   | `300`   | Time between checks (seconds)   |
| `max_retries`       | int   | `3`     | Retry attempts for failures     |
| `retry_delay`       | int   | `5`     | Delay between retries (seconds) |

## 🔔 Alert Setup

### Desktop Notifications

**Windows:**

```bash
pip install win10toast
```

**macOS:**
Built-in, no setup needed!

**Linux:**

```bash
sudo apt-get install libnotify-bin  # Ubuntu/Debian
sudo dnf install libnotify           # Fedora
```

### Email Alerts (Gmail)

1. Enable 2-Factor Authentication
2. Generate App Password:
    - Google Account → Security
    - 2-Step Verification → App passwords
    - Generate for "Mail"
3. Update config:

```json
{
    "alerts": {
        "email": {
            "enabled": true,
            "smtp_user": "your-email@gmail.com",
            "smtp_password": "xxxx xxxx xxxx xxxx"
        }
    }
}
```

### Slack Notifications

1. Create Incoming Webhook at https://api.slack.com/apps
2. Copy webhook URL
3. Update config:

```json
{
    "alerts": {
        "slack": {
            "enabled": true,
            "webhook_url": "https://hooks.slack.com/services/..."
        }
    }
}
```

### Custom Webhooks

Your endpoint will receive POST requests:

```json
{
    "url": "https://example.com",
    "status": "DOWN",
    "http_code": 500,
    "response_time_ms": 0,
    "message": "Server error",
    "timestamp": "2025-01-22T10:30:00Z"
}
```

## 📊 Reports & Logs

### Log Files

```
website-monitor/
├── logs/
│   ├── monitor_20250122.log    # Daily activity log
│   └── status.jsonl             # Check results (JSON)
└── reports/
    └── weekly-report.txt        # Generated reports
```

### Viewing Logs

**Windows:**

```powershell
# Last 20 entries
Get-Content logs\status.jsonl -Tail 20

# Today's log
type logs\monitor_$(Get-Date -Format 'yyyyMMdd').log

# Follow live
Get-Content logs\status.jsonl -Wait -Tail 10
```

**macOS/Linux:**

```bash
# Last 20 entries
tail -20 logs/status.jsonl

# Today's log
cat logs/monitor_$(date +%Y%m%d).log

# Follow live
tail -f logs/status.jsonl
```

### Generating Reports

```bash
# Default (24 hours)
python monitor.py report

# Custom timeframe
python monitor.py report --hours 168   # Week
python monitor.py report --hours 720   # Month

# Save to file
python monitor.py report > reports/daily.txt

# Email report (Unix/Mac)
python monitor.py report | mail -s "Monitor Report" admin@example.com
```

### Sample Report Output

```
==================================================
Website Monitor Report - Last 24 hours
Generated: 2025-01-22 10:30:00
==================================================

Website: https://www.example.com
--------------------------------------------------
Uptime: 99.65%
Total checks: 288
UP: 287 | DOWN: 1 | SLOW: 0 | ERROR: 0
Avg response time: 234ms
Min/Max: 180ms / 890ms

Website: https://api.example.com
--------------------------------------------------
Uptime: 100.00%
Total checks: 288
UP: 288 | DOWN: 0 | SLOW: 0 | ERROR: 0
Avg response time: 156ms
Min/Max: 120ms / 245ms

==================================================
```

## 🔍 Troubleshooting

### Common Issues

#### Python Not Found

**Windows:**

- Reinstall Python with "Add to PATH" checked
- Or use full path: `C:\Python39\python.exe monitor.py check`

**macOS/Linux:**

- Use `python3` instead of `python`
- Install: see platform-specific installation above

#### Permission Denied

**Unix/Mac:**

```bash
chmod +x monitor.py
```

**Windows:**
Run PowerShell as Administrator

#### SSL Certificate Errors

For development/testing only:

```python
# Edit monitor.py, find ssl_context creation
self.ssl_context.check_hostname = False
self.ssl_context.verify_mode = ssl.CERT_NONE
```

#### Desktop Notifications Not Working

**Windows:**

```bash
pip install win10toast
```

**Linux:**

```bash
sudo apt-get install libnotify-bin
```

**macOS:**
Check System Preferences → Notifications → Script Editor

#### Email Alerts Failing

1. Verify SMTP settings
2. Use Gmail App Password (not regular password)
3. Check firewall/antivirus blocking port 587
4. Test SMTP connection:

```python
import smtplib
server = smtplib.SMTP('smtp.gmail.com', 587)
server.starttls()
server.login('email@gmail.com', 'app-password')
print("✓ SMTP works!")
server.quit()
```

## 🎯 Best Practices

### 1. Start Small

```json
{
    "websites": ["https://www.yoursite.com"]
}
```

### 2. Set Realistic Thresholds

```json
{
    "timeout": 10,
    "max_response_time": 3000
}
```

### 3. Schedule Appropriately

- Critical sites: Every 1-5 minutes
- Normal monitoring: Every 5-15 minutes
- Low priority: Every 30-60 minutes

### 4. Manage Alerts

- Enable desktop for development
- Use email for production
- Slack for team collaboration
- Webhooks for automation

### 5. Regular Maintenance

```bash
# Weekly cleanup (keep last 30 days)
find logs/ -name "*.log" -mtime +30 -delete

# Monthly reports
python monitor.py report --hours 720 > reports/monthly-$(date +%Y-%m).txt
```

## 📚 Architecture & Learning

### HTTP Status Codes

| Code | Status   | Action               |
| ---- | -------- | -------------------- |
| 2xx  | UP       | ✅ Normal operation   |
| 3xx  | REDIRECT | ℹ️ May need attention |
| 4xx  | ERROR    | ⚠️ Client error       |
| 5xx  | DOWN     | 🚨 Server error       |
| 000  | DOWN     | 🚨 Connection failed  |

### Why Python for Cross-Platform?

1. **Write Once, Run Anywhere** - Same code, all OSes
2. **Built-in Libraries** - No external dependencies required
3. **Easy Deployment** - Single file distribution
4. **Platform Detection** - Automatic OS adaptation
5. **Rich Ecosystem** - Optional integrations available

## 🚀 Advanced Usage

### Integration with CI/CD

```yaml
# .gitlab-ci.yml
deploy:
    script:
        - ./deploy.sh
        - python monitor.py check
        - if [ $? -ne 0 ]; then ./rollback.sh; fi
```

### Docker Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY monitor.py monitor_config.json ./
RUN mkdir logs reports
CMD ["python", "monitor.py", "monitor"]
```

```bash
docker build -t website-monitor .
docker run -d --name monitor website-monitor
```

### Monitoring Multiple Environments

```bash
# Production
python monitor.py check --config prod-config.json

# Staging
python monitor.py check --config staging-config.json

# Development
python monitor.py check --config dev-config.json
```

## 📈 Roadmap

- [*] SSL certificate expiry monitoring
- [ ] Multi-region checking
- [ ] Web dashboard
- [ ] Prometheus metrics export
- [ ] Custom HTTP headers support
- [ ] POST request monitoring
- [ ] GraphQL endpoint checks

## 🤝 Contributing

This is a learning project! Feel free to:

- Add features
- Improve cross-platform compatibility
- Enhance documentation
- Report issues

## 📄 License

MIT License - Use freely for learning and production!

---

## 🎓 What You Learned

✅ **Cross-platform development** - One solution for all OSes
✅ **HTTP monitoring** - Status codes, response times
✅ **Python scripting** - Real-world automation
✅ **Task scheduling** - Cron, Task Scheduler, launchd, systemd
✅ **Alert systems** - Multi-channel notifications
✅ **DevOps practices** - Monitoring, logging, reporting

---

**Built with ❤️ for cross-platform DevOps**

For questions: Check the troubleshooting section or review the inline code comments!
