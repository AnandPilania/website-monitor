#!/usr/bin/env python3

"""
Cross-Platform Website Monitor
Works on Windows, macOS, and Linux/Unix
Monitors website availability and performance
"""

import os
import sys
import json
import time
import logging
import argparse
import platform
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import ssl

# ============================================================================
# Configuration
# ============================================================================

class Config:
    """Configuration management"""

    def __init__(self, config_path: Optional[Path] = None):
        self.script_dir = Path(__file__).parent
        self.config_path = config_path or self.script_dir / "monitor_config.json"
        self.log_dir = self.script_dir / "logs"
        self.reports_dir = self.script_dir / "reports"

        # Create directories
        self.log_dir.mkdir(exist_ok=True)
        self.reports_dir.mkdir(exist_ok=True)

        # Default configuration
        self.config = {
            "websites": [
                "https://www.google.com",
                "https://www.github.com"
            ],
            "timeout": 10,
            "max_response_time": 5000,
            "check_interval": 300,
            "max_retries": 3,
            "retry_delay": 5,
            "alerts": {
                "email": {
                    "enabled": False,
                    "to": "admin@example.com",
                    "from": "monitor@example.com",
                    "smtp_host": "smtp.gmail.com",
                    "smtp_port": 587,
                    "smtp_user": "",
                    "smtp_password": ""
                },
                "webhook": {
                    "enabled": False,
                    "url": ""
                },
                "slack": {
                    "enabled": False,
                    "webhook_url": ""
                },
                "desktop": {
                    "enabled": True
                }
            }
        }

        # Load or create config
        self.load_config()

    def load_config(self):
        """Load configuration from file"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    loaded = json.load(f)
                    self.config.update(loaded)
                logging.info(f"Configuration loaded from {self.config_path}")
            except Exception as e:
                logging.error(f"Error loading config: {e}")
                self.save_config()  # Create default config
        else:
            self.save_config()
            logging.info(f"Default configuration created at {self.config_path}")

    def save_config(self):
        """Save configuration to file"""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config, f, indent=4)
            logging.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logging.error(f"Error saving config: {e}")

    def get(self, key: str, default=None):
        """Get configuration value"""
        return self.config.get(key, default)


# ============================================================================
# Logging Setup
# ============================================================================

def setup_logging(log_dir: Path):
    """Setup logging configuration"""
    log_file = log_dir / f"monitor_{datetime.now().strftime('%Y%m%d')}.log"

    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_formatter = logging.Formatter('%(message)s')

    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    # Setup root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


# ============================================================================
# Website Checker
# ============================================================================

class WebsiteChecker:
    """Website availability checker"""

    def __init__(self, config: Config):
        self.config = config
        self.timeout = config.get('timeout', 10)
        self.max_response_time = config.get('max_response_time', 5000)

        # Create SSL context that doesn't verify certificates (for testing)
        self.ssl_context = ssl.create_default_context()
        # For production, keep verification enabled
        # self.ssl_context.check_hostname = False
        # self.ssl_context.verify_mode = ssl.CERT_NONE

    def check_website(self, url: str) -> Dict:
        """
        Check a single website
        Returns: dict with status, http_code, response_time, message
        """
        result = {
            'url': url,
            'status': 'UNKNOWN',
            'http_code': 0,
            'response_time_ms': 0,
            'message': '',
            'timestamp': datetime.now().isoformat()
        }

        try:
            # Create request
            req = Request(url)
            req.add_header('User-Agent', 'Website-Monitor/1.0')

            # Measure response time
            start_time = time.time()

            try:
                response = urlopen(req, timeout=self.timeout, context=self.ssl_context)
                response_time = (time.time() - start_time) * 1000
                http_code = response.getcode()

                result['http_code'] = http_code
                result['response_time_ms'] = int(response_time)

                # Determine status
                if 200 <= http_code < 300:
                    if response_time <= self.max_response_time:
                        result['status'] = 'UP'
                        result['message'] = f"OK ({result['response_time_ms']}ms)"
                    else:
                        result['status'] = 'SLOW'
                        result['message'] = f"Slow response ({result['response_time_ms']}ms > {self.max_response_time}ms)"
                elif 300 <= http_code < 400:
                    result['status'] = 'REDIRECT'
                    result['message'] = f"Redirect detected (HTTP {http_code})"
                elif 400 <= http_code < 500:
                    result['status'] = 'ERROR'
                    result['message'] = f"Client error (HTTP {http_code})"
                else:
                    result['status'] = 'DOWN'
                    result['message'] = f"Server error (HTTP {http_code})"

            except HTTPError as e:
                result['http_code'] = e.code
                result['status'] = 'DOWN'
                result['message'] = f"HTTP Error {e.code}: {e.reason}"
            except URLError as e:
                result['status'] = 'DOWN'
                result['message'] = f"Connection failed: {str(e.reason)}"
            except Exception as e:
                result['status'] = 'DOWN'
                result['message'] = f"Error: {str(e)}"

        except Exception as e:
            result['status'] = 'DOWN'
            result['message'] = f"Unexpected error: {str(e)}"

        return result

    def check_with_retry(self, url: str) -> Dict:
        """Check website with retry logic"""
        max_retries = self.config.get('max_retries', 3)
        retry_delay = self.config.get('retry_delay', 5)

        for attempt in range(1, max_retries + 1):
            result = self.check_website(url)

            # If successful or definitive error, return
            if result['status'] in ['UP', 'SLOW', 'ERROR']:
                return result

            # If down and retries remain, wait and retry
            if attempt < max_retries:
                logging.warning(f"Retry {attempt}/{max_retries} for {url} after {retry_delay}s delay")
                time.sleep(retry_delay)

        return result


# ============================================================================
# Alert Manager
# ============================================================================

class AlertManager:
    """Manages different types of alerts"""

    def __init__(self, config: Config):
        self.config = config
        self.alerts_config = config.get('alerts', {})

    def send_alerts(self, result: Dict):
        """Send alerts based on result status"""
        if result['status'] in ['DOWN', 'ERROR']:
            self.send_desktop_notification(result)
            self.send_email_alert(result)
            self.send_webhook_alert(result)
            self.send_slack_alert(result)

    def send_desktop_notification(self, result: Dict):
        """Send desktop notification (cross-platform)"""
        if not self.alerts_config.get('desktop', {}).get('enabled', True):
            return

        try:
            title = f"Website Monitor Alert: {result['status']}"
            message = f"{result['url']}\n{result['message']}"

            system = platform.system()

            if system == 'Darwin':  # macOS
                script = f'display notification "{message}" with title "{title}"'
                subprocess.run(['osascript', '-e', script], check=False)
            elif system == 'Windows':
                # Windows 10+ toast notification
                try:
                    from win10toast import ToastNotifier
                    toaster = ToastNotifier()
                    toaster.show_toast(title, message, duration=10, threaded=True)
                except ImportError:
                    logging.warning("win10toast not installed. Install with: pip install win10toast")
            elif system == 'Linux':
                # Linux notify-send
                subprocess.run(['notify-send', title, message], check=False)

            logging.info("Desktop notification sent")
        except Exception as e:
            logging.debug(f"Desktop notification failed: {e}")

    def send_email_alert(self, result: Dict):
        """Send email alert"""
        email_config = self.alerts_config.get('email', {})
        if not email_config.get('enabled', False):
            return

        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart()
            msg['From'] = email_config.get('from', 'monitor@example.com')
            msg['To'] = email_config.get('to', 'admin@example.com')
            msg['Subject'] = f"[ALERT] Website Monitor: {result['url']} is {result['status']}"

            body = f"""
Website Monitoring Alert

URL: {result['url']}
Status: {result['status']}
HTTP Code: {result['http_code']}
Response Time: {result['response_time_ms']}ms
Message: {result['message']}
Timestamp: {result['timestamp']}

--
Website Monitor
"""
            msg.attach(MIMEText(body, 'plain'))

            # Send email
            server = smtplib.SMTP(email_config.get('smtp_host', 'smtp.gmail.com'),
                                 email_config.get('smtp_port', 587))
            server.starttls()
            server.login(email_config.get('smtp_user', ''),
                        email_config.get('smtp_password', ''))
            server.send_message(msg)
            server.quit()

            logging.info(f"Email alert sent to {email_config['to']}")
        except Exception as e:
            logging.error(f"Failed to send email: {e}")

    def send_webhook_alert(self, result: Dict):
        """Send webhook alert"""
        webhook_config = self.alerts_config.get('webhook', {})
        if not webhook_config.get('enabled', False):
            return

        try:
            import urllib.request

            webhook_url = webhook_config.get('url', '')
            if not webhook_url:
                return

            payload = json.dumps(result).encode('utf-8')
            req = urllib.request.Request(
                webhook_url,
                data=payload,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            urllib.request.urlopen(req, timeout=5)
            logging.info("Webhook notification sent")
        except Exception as e:
            logging.error(f"Failed to send webhook: {e}")

    def send_slack_alert(self, result: Dict):
        """Send Slack notification"""
        slack_config = self.alerts_config.get('slack', {})
        if not slack_config.get('enabled', False):
            return

        try:
            import urllib.request

            webhook_url = slack_config.get('webhook_url', '')
            if not webhook_url:
                return

            color = 'danger' if result['status'] == 'DOWN' else 'warning'

            payload = {
                "attachments": [{
                    "color": color,
                    "title": "Website Monitor Alert",
                    "fields": [
                        {"title": "Website", "value": result['url'], "short": False},
                        {"title": "Status", "value": result['status'], "short": True},
                        {"title": "Details", "value": result['message'], "short": True}
                    ],
                    "footer": "Website Monitor",
                    "ts": int(time.time())
                }]
            }

            req = urllib.request.Request(
                webhook_url,
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            urllib.request.urlopen(req, timeout=5)
            logging.info("Slack notification sent")
        except Exception as e:
            logging.error(f"Failed to send Slack notification: {e}")


# ============================================================================
# Status Logger
# ============================================================================

class StatusLogger:
    """Logs monitoring results"""

    def __init__(self, log_dir: Path):
        self.status_file = log_dir / "status.jsonl"

    def log_result(self, result: Dict):
        """Log check result to file"""
        try:
            with open(self.status_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(result) + '\n')
        except Exception as e:
            logging.error(f"Failed to log result: {e}")

    def get_history(self, hours: int = 24) -> List[Dict]:
        """Get monitoring history for specified hours"""
        if not self.status_file.exists():
            return []

        cutoff = datetime.now() - timedelta(hours=hours)
        results = []

        try:
            with open(self.status_file, 'r', encoding='utf-8') as f:
                for line in f:
                    try:
                        result = json.loads(line.strip())
                        timestamp = datetime.fromisoformat(result['timestamp'])
                        if timestamp >= cutoff:
                            results.append(result)
                    except:
                        continue
        except Exception as e:
            logging.error(f"Failed to read history: {e}")

        return results


# ============================================================================
# Reporter
# ============================================================================

class Reporter:
    """Generates monitoring reports"""

    def __init__(self, status_logger: StatusLogger):
        self.status_logger = status_logger

    def generate_report(self, hours: int = 24) -> str:
        """Generate uptime report"""
        history = self.status_logger.get_history(hours)

        if not history:
            return "No monitoring data available"

        # Group by URL
        url_stats = {}
        for result in history:
            url = result['url']
            if url not in url_stats:
                url_stats[url] = {
                    'total': 0,
                    'up': 0,
                    'down': 0,
                    'slow': 0,
                    'error': 0,
                    'response_times': []
                }

            stats = url_stats[url]
            stats['total'] += 1

            status = result['status']
            if status == 'UP':
                stats['up'] += 1
            elif status == 'DOWN':
                stats['down'] += 1
            elif status == 'SLOW':
                stats['slow'] += 1
            elif status == 'ERROR':
                stats['error'] += 1

            if result.get('response_time_ms', 0) > 0:
                stats['response_times'].append(result['response_time_ms'])

        # Generate report
        report = []
        report.append("=" * 50)
        report.append(f"Website Monitor Report - Last {hours} hours")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append("=" * 50)

        for url, stats in url_stats.items():
            report.append(f"\nWebsite: {url}")
            report.append("-" * 50)

            if stats['total'] > 0:
                uptime = ((stats['up'] + stats['slow']) / stats['total']) * 100
                report.append(f"Uptime: {uptime:.2f}%")
                report.append(f"Total checks: {stats['total']}")
                report.append(f"UP: {stats['up']} | DOWN: {stats['down']} | SLOW: {stats['slow']} | ERROR: {stats['error']}")

                if stats['response_times']:
                    avg_time = sum(stats['response_times']) / len(stats['response_times'])
                    report.append(f"Avg response time: {int(avg_time)}ms")
                    report.append(f"Min/Max: {min(stats['response_times'])}ms / {max(stats['response_times'])}ms")

        report.append("\n" + "=" * 50)

        return '\n'.join(report)


# ============================================================================
# Monitor
# ============================================================================

class WebsiteMonitor:
    """Main monitoring orchestrator"""

    def __init__(self, config: Config):
        self.config = config
        self.checker = WebsiteChecker(config)
        self.alert_manager = AlertManager(config)
        self.status_logger = StatusLogger(config.log_dir)
        self.reporter = Reporter(self.status_logger)

    def check_all(self):
        """Run checks on all configured websites"""
        websites = self.config.get('websites', [])

        if not websites:
            logging.error("No websites configured for monitoring")
            return

        print("\n" + "=" * 50)
        print(f"Website Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50 + "\n")

        for url in websites:
            result = self.checker.check_with_retry(url)

            # Log result
            self.status_logger.log_result(result)
            logging.info(f"Check: {url} | Status: {result['status']} | "
                        f"HTTP: {result['http_code']} | Time: {result['response_time_ms']}ms")

            # Display result
            self._display_result(result)

            # Send alerts if needed
            self.alert_manager.send_alerts(result)

        print("=" * 50 + "\n")

    def _display_result(self, result: Dict):
        """Display check result with colors"""
        status = result['status']

        # Color codes for different systems
        if platform.system() == 'Windows':
            # Windows console colors
            symbols = {'UP': '[OK]', 'DOWN': '[!!]', 'SLOW': '[!!]', 'ERROR': '[!!]'}
        else:
            # Unix-like systems with ANSI colors
            symbols = {'UP': '✓', 'DOWN': '✗', 'SLOW': '⚠', 'ERROR': '✗'}

        symbol = symbols.get(status, '?')
        print(f"{symbol} {result['url']} - {status}: {result['message']}")

    def monitor_continuous(self):
        """Run continuous monitoring"""
        interval = self.config.get('check_interval', 300)

        logging.info(f"Starting continuous monitoring (interval: {interval}s)")
        print(f"\nMonitoring started. Checking every {interval} seconds.")
        print("Press Ctrl+C to stop.\n")

        try:
            while True:
                self.check_all()
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\nMonitoring stopped by user.")
            logging.info("Monitoring stopped by user")


# ============================================================================
# CLI Interface
# ============================================================================

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Cross-Platform Website Monitor',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s check              Run single check
  %(prog)s monitor            Start continuous monitoring
  %(prog)s report             Generate 24h report
  %(prog)s report --hours 168 Generate weekly report
  %(prog)s test               Test configuration
        """
    )

    parser.add_argument(
        'command',
        choices=['check', 'monitor', 'report', 'test', 'config'],
        help='Command to execute'
    )
    parser.add_argument(
        '--hours',
        type=int,
        default=24,
        help='Hours for report (default: 24)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to config file'
    )

    args = parser.parse_args()

    # Initialize
    config_path = Path(args.config) if args.config else None
    config = Config(config_path)
    setup_logging(config.log_dir)

    monitor = WebsiteMonitor(config)

    # Execute command
    if args.command == 'check':
        monitor.check_all()

    elif args.command == 'monitor':
        monitor.monitor_continuous()

    elif args.command == 'report':
        print(monitor.reporter.generate_report(args.hours))

    elif args.command == 'test':
        print("Testing configuration...")
        print(f"Config file: {config.config_path}")
        print(f"Log directory: {config.log_dir}")
        print(f"Websites: {len(config.get('websites', []))}")
        print("\nRunning test check...\n")
        monitor.check_all()

    elif args.command == 'config':
        print(f"Configuration file: {config.config_path}")
        print("\nCurrent configuration:")
        print(json.dumps(config.config, indent=2))


if __name__ == '__main__':
    main()
