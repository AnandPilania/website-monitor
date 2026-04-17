#!/usr/bin/env python3
"""
Cross-Platform Website Monitor
Supports Windows, macOS, and Linux/Unix

Usage:
    python monitor.py check              # Single check all sites
    python monitor.py monitor            # Continuous monitoring
    python monitor.py report             # Last 24h uptime report
    python monitor.py report --hours 168 # Weekly report
    python monitor.py test               # Test config + connectivity
    python monitor.py config             # Show resolved configuration
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import signal
import smtplib
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__ = "0.0.2"

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    url: str
    status: str           # UP | DOWN | SLOW | REDIRECT | ERROR | UNKNOWN
    http_code: int
    response_time_ms: int
    message: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    keyword_found: Optional[bool] = None  # None = not checked
    ssl_days_remaining: Optional[int] = None

    def is_healthy(self) -> bool:
        return self.status in ("UP", "REDIRECT")

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "CheckResult":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ENV_MAP = {
    # env var                        config key path (dot-separated)
    "MONITOR_SMTP_PASSWORD":         "alerts.email.smtp_password",
    "MONITOR_SMTP_USER":             "alerts.email.smtp_user",
    "MONITOR_SLACK_WEBHOOK":         "alerts.slack.webhook_url",
    "MONITOR_WEBHOOK_URL":           "alerts.webhook.url",
}

DEFAULT_CONFIG: Dict = {
    "websites": [
        "https://www.google.com",
        "https://www.github.com",
    ],
    "timeout": 10,
    "max_response_time": 5000,
    "check_interval": 300,
    "max_retries": 3,
    "retry_delay": 2,
    "retry_backoff": 2.0,          # exponential backoff multiplier
    "concurrent_checks": 5,        # parallel workers
    "keyword_checks": {},          # {"https://example.com": "keyword"}
    "alerts": {
        "email": {
            "enabled": False,
            "to": "admin@example.com",
            "from": "monitor@example.com",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",   # prefer MONITOR_SMTP_PASSWORD env var
        },
        "webhook": {
            "enabled": False,
            "url": "",             # prefer MONITOR_WEBHOOK_URL env var
            "timeout": 5,
        },
        "slack": {
            "enabled": False,
            "webhook_url": "",     # prefer MONITOR_SLACK_WEBHOOK env var
        },
        "desktop": {
            "enabled": True,
        },
    },
}


def _nested_set(d: Dict, dotted_key: str, value: str) -> None:
    """Set a value in a nested dict using a dot-separated key path."""
    keys = dotted_key.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge *override* into *base* (non-destructive copy)."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class Config:
    """Load, validate, and expose monitor configuration."""

    def __init__(self, config_path: Optional[Path] = None):
        self.script_dir = Path(__file__).parent.resolve()
        self.config_path = config_path or self.script_dir / "monitor_config.json"
        self.log_dir = self.script_dir / "logs"
        self.reports_dir = self.script_dir / "reports"

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self._data: Dict = _deep_merge(DEFAULT_CONFIG, {})

        self._load()
        self._apply_env_overrides()
        self._validate()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    on_disk = json.load(f)
                self._data = _deep_merge(DEFAULT_CONFIG, on_disk)
                logging.debug("Config loaded from %s", self.config_path)
            except json.JSONDecodeError as exc:
                logging.error("Config JSON parse error: %s — using defaults", exc)
        else:
            self._save_defaults()

    def _save_defaults(self) -> None:
        try:
            # Don't persist secrets to disk
            safe = _deep_merge(self._data, {})
            safe["alerts"]["email"]["smtp_password"] = ""
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(safe, f, indent=4)
            logging.info("Default config written to %s", self.config_path)
        except OSError as exc:
            logging.warning("Could not write default config: %s", exc)

    # ------------------------------------------------------------------
    # Env overrides (secrets should never live in config files)
    # ------------------------------------------------------------------

    def _apply_env_overrides(self) -> None:
        for env_var, dotted_key in _ENV_MAP.items():
            value = os.environ.get(env_var)
            if value:
                _nested_set(self._data, dotted_key, value)
                logging.debug("Config override from env: %s", env_var)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self) -> None:
        websites = self._data.get("websites", [])
        if not isinstance(websites, list):
            raise ValueError("'websites' must be a list")
        for url in websites:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                raise ValueError(
                    f"Invalid URL scheme in '{url}': must be http or https"
                )

        for key in ("timeout", "max_response_time", "check_interval",
                    "max_retries", "retry_delay", "concurrent_checks"):
            val = self._data.get(key)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(
                    f"Config key '{key}' must be a positive number, got: {val!r}"
                )

    # ------------------------------------------------------------------
    # Access helpers
    # ------------------------------------------------------------------

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def get_nested(self, *keys, default=None):
        d = self._data
        for k in keys:
            if not isinstance(d, dict):
                return default
            d = d.get(k, {})
        return d if d != {} else default

    @property
    def websites(self) -> List[str]:
        return self._data.get("websites", [])

    def __repr__(self) -> str:
        safe = _deep_merge(self._data, {})
        pwd = safe["alerts"]["email"]["smtp_password"]
        safe["alerts"]["email"]["smtp_password"] = "***" if pwd else ""
        return json.dumps(safe, indent=2)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    log_file = log_dir / f"monitor_{datetime.now():%Y%m%d}.log"

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=7,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%S")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


# ---------------------------------------------------------------------------
# SSL helper
# ---------------------------------------------------------------------------

def _ssl_days_remaining(
    hostname: str, port: int = 443, timeout: int = 5
) -> Optional[int]:
    """Return days until SSL cert expiry, or None on failure."""
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(
            __import__("socket").create_connection((hostname, port), timeout=timeout),
            server_hostname=hostname,
        ) as s:
            cert = s.getpeercert()
            expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            return (expiry - datetime.utcnow()).days
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Website checker
# ---------------------------------------------------------------------------

class WebsiteChecker:
    """HTTP(S) availability and performance checker."""

    def __init__(self, config: Config):
        self.config = config
        self.timeout = config.get("timeout", 10)
        self.max_response_time = config.get("max_response_time", 5000)
        self.keyword_checks: Dict[str, str] = config.get("keyword_checks") or {}

        self._ssl_ctx = ssl.create_default_context()

    # ------------------------------------------------------------------

    def check_once(self, url: str) -> CheckResult:
        """Single HTTP check — no retries."""
        result = CheckResult(url=url, status="UNKNOWN", http_code=0,
                             response_time_ms=0, message="")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": f"WebsiteMonitor/{__version__}"}
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_ctx
            ) as resp:
                body = resp.read()
                elapsed = int((time.monotonic() - t0) * 1000)
                code = resp.status

            result.http_code = code
            result.response_time_ms = elapsed

            if 200 <= code < 300:
                if elapsed > self.max_response_time:
                    result.status = "SLOW"
                    result.message = (
                        f"Slow response ({elapsed}ms > {self.max_response_time}ms)"
                    )
                else:
                    result.status = "UP"
                    result.message = f"OK ({elapsed}ms)"
            elif 300 <= code < 400:
                result.status = "REDIRECT"
                result.message = f"Redirect (HTTP {code})"
            elif 400 <= code < 500:
                result.status = "ERROR"
                result.message = f"Client error (HTTP {code})"
            else:
                result.status = "DOWN"
                result.message = f"Server error (HTTP {code})"

            # Keyword check
            kw = self.keyword_checks.get(url)
            if kw and result.status in ("UP", "SLOW"):
                result.keyword_found = kw.encode() in body

        except urllib.error.HTTPError as exc:
            result.http_code = exc.code
            result.status = "DOWN"
            result.message = f"HTTP {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            result.status = "DOWN"
            result.message = f"Connection failed: {exc.reason}"
        except TimeoutError:
            result.status = "DOWN"
            result.message = f"Timed out after {self.timeout}s"
        except Exception as exc:  # noqa: BLE001
            result.status = "DOWN"
            result.message = f"Unexpected error: {exc}"

        # SSL expiry (only for https, only when UP/SLOW)
        parsed = urlparse(url)
        if parsed.scheme == "https" and result.status in ("UP", "SLOW"):
            result.ssl_days_remaining = _ssl_days_remaining(
                parsed.hostname, parsed.port or 443, self.timeout
            )

        return result

    # ------------------------------------------------------------------

    def check_with_retry(self, url: str) -> CheckResult:
        """Check with exponential back-off retry."""
        max_retries: int = self.config.get("max_retries", 3)
        delay: float = self.config.get("retry_delay", 2)
        backoff: float = self.config.get("retry_backoff", 2.0)

        result = CheckResult(url=url, status="UNKNOWN", http_code=0,
                             response_time_ms=0, message="")
        for attempt in range(1, max_retries + 1):
            result = self.check_once(url)
            if result.is_healthy() or result.status == "ERROR":
                return result
            if attempt < max_retries:
                logging.debug(
                    "Retry %d/%d for %s in %.0fs", attempt, max_retries, url, delay
                )
                time.sleep(delay)
                delay *= backoff
        return result

    # ------------------------------------------------------------------

    def check_all(self, urls: List[str]) -> List[CheckResult]:
        """Check all URLs in parallel."""
        workers = min(self.config.get("concurrent_checks", 5), len(urls))
        results: List[CheckResult] = []
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="checker"
        ) as pool:
            futures = {pool.submit(self.check_with_retry, url): url for url in urls}
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    url = futures[future]
                    results.append(CheckResult(url=url, status="UNKNOWN", http_code=0,
                                               response_time_ms=0, message=str(exc)))
        # Preserve original order
        order = {url: i for i, url in enumerate(urls)}
        results.sort(key=lambda r: order.get(r.url, 999))
        return results


# ---------------------------------------------------------------------------
# Alert manager
# ---------------------------------------------------------------------------

class AlertManager:
    """Multi-channel alerting (desktop, email, webhook, Slack)."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()  # prevent alert storms

    def send_alerts(self, result: CheckResult) -> None:
        if result.status not in ("DOWN", "ERROR"):
            return
        with self._lock:
            self._desktop(result)
            self._email(result)
            self._webhook(result)
            self._slack(result)

    # ------------------------------------------------------------------
    # Desktop
    # ------------------------------------------------------------------

    def _desktop(self, result: CheckResult) -> None:
        if not self.config.get_nested("alerts", "desktop", "enabled", default=True):
            return
        title = f"Monitor Alert: {result.status}"
        body = f"{result.url}\n{result.message}"
        try:
            system = platform.system()
            if system == "Darwin":
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'display notification "{body}" with title "{title}"',
                    ],
                    check=False,
                    capture_output=True,
                )
            elif system == "Windows":
                try:
                    from win10toast import ToastNotifier  # type: ignore
                    ToastNotifier().show_toast(title, body, duration=10, threaded=True)
                except ImportError:
                    # Fallback: Windows 10+ built-in via PowerShell
                    ps = (
                        "[Windows.UI.Notifications.ToastNotificationManager,"
                        "Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
                        "$xml=[Windows.UI.Notifications.ToastNotificationManager]::"
                        "GetTemplateContent([Windows.UI.Notifications."
                        "ToastTemplateType]::ToastText01);"
                        f"$xml.GetElementsByTagName('text')[0].InnerText="
                        f"'{title}: {body}';"
                        "$notif=[Windows.UI.Notifications.ToastNotification]"
                        "::new($xml);"
                        "[Windows.UI.Notifications.ToastNotificationManager]::"
                        "CreateToastNotifier('Website Monitor').Show($notif)"
                    )
                    subprocess.run(["powershell", "-Command", ps],
                                   check=False, capture_output=True)
            elif system == "Linux":
                subprocess.run(["notify-send", "-u", "critical", title, body],
                               check=False, capture_output=True)
        except Exception as exc:  # noqa: BLE001
            logging.debug("Desktop notification failed: %s", exc)

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------

    def _email(self, result: CheckResult) -> None:
        cfg = self.config.get_nested("alerts", "email") or {}
        if not cfg.get("enabled"):
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = cfg.get("from", "monitor@example.com")
            msg["To"] = cfg["to"]
            msg["Subject"] = f"[ALERT] {result.url} is {result.status}"
            ssl_line = ""
            if result.ssl_days_remaining is not None:
                ssl_line = f"SSL Expiry  : {result.ssl_days_remaining} days remaining\n"
            body = (
                f"Website Monitoring Alert\n"
                f"{'='*40}\n"
                f"URL         : {result.url}\n"
                f"Status      : {result.status}\n"
                f"HTTP Code   : {result.http_code}\n"
                f"Response    : {result.response_time_ms}ms\n"
                f"{ssl_line}"
                f"Message     : {result.message}\n"
                f"Timestamp   : {result.timestamp}\n"
                f"{'='*40}\n"
                f"-- Website Monitor v{__version__}\n"
            )
            msg.attach(MIMEText(body, "plain"))
            with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"),
                              cfg.get("smtp_port", 587)) as server:
                server.ehlo()
                server.starttls()
                server.login(cfg["smtp_user"], cfg["smtp_password"])
                server.send_message(msg)
            logging.info("Email alert sent to %s", cfg["to"])
        except Exception as exc:  # noqa: BLE001
            logging.error("Email alert failed: %s", exc)

    # ------------------------------------------------------------------
    # Generic HTTP webhook
    # ------------------------------------------------------------------

    def _webhook(self, result: CheckResult) -> None:
        cfg = self.config.get_nested("alerts", "webhook") or {}
        if not cfg.get("enabled") or not cfg.get("url"):
            return
        try:
            payload = json.dumps(result.to_dict()).encode()
            req = urllib.request.Request(
                cfg["url"], data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": f"WebsiteMonitor/{__version__}"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=cfg.get("timeout", 5))
            logging.info("Webhook alert sent")
        except Exception as exc:  # noqa: BLE001
            logging.error("Webhook alert failed: %s", exc)

    # ------------------------------------------------------------------
    # Slack
    # ------------------------------------------------------------------

    def _slack(self, result: CheckResult) -> None:
        cfg = self.config.get_nested("alerts", "slack") or {}
        if not cfg.get("enabled") or not cfg.get("webhook_url"):
            return
        try:
            color = "danger" if result.status == "DOWN" else "warning"
            fields = [
                {"title": "Website", "value": result.url, "short": False},
                {"title": "Status", "value": result.status, "short": True},
                {"title": "HTTP Code", "value": str(result.http_code), "short": True},
                {
                    "title": "Response Time",
                    "value": f"{result.response_time_ms}ms",
                    "short": True,
                },
                {"title": "Details", "value": result.message, "short": False},
            ]
            if result.ssl_days_remaining is not None:
                fields.append({
                    "title": "SSL Expiry",
                    "value": f"{result.ssl_days_remaining} days",
                    "short": True,
                })
            payload = json.dumps({
                "attachments": [{
                    "color": color,
                    "title": ":rotating_light: Website Monitor Alert",
                    "fields": fields,
                    "footer": f"WebsiteMonitor v{__version__}",
                    "ts": int(time.time()),
                }]
            }).encode()
            req = urllib.request.Request(
                cfg["webhook_url"], data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            logging.info("Slack alert sent")
        except Exception as exc:  # noqa: BLE001
            logging.error("Slack alert failed: %s", exc)


# ---------------------------------------------------------------------------
# Status logger (JSON Lines)
# ---------------------------------------------------------------------------

class StatusLogger:
    """Persist check results as newline-delimited JSON."""

    def __init__(self, log_dir: Path):
        self._file = log_dir / "status.jsonl"
        self._lock = threading.Lock()

    def log(self, result: CheckResult) -> None:
        try:
            with self._lock:
                with open(self._file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result.to_dict()) + "\n")
        except OSError as exc:
            logging.error("Failed to write status log: %s", exc)

    def history(self, hours: int = 24) -> List[CheckResult]:
        if not self._file.exists():
            return []
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        results: List[CheckResult] = []
        try:
            with open(self._file, encoding="utf-8") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        d = json.loads(raw)
                        ts_str = d.get("timestamp", "")
                        # Support both "Z" suffix and naive ISO
                        ts_str = ts_str.rstrip("Z").split("+")[0]
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            results.append(CheckResult.from_dict(d))
                    except Exception:  # noqa: BLE001
                        continue
        except OSError as exc:
            logging.error("Failed to read status log: %s", exc)
        return results

    def rotate(self, keep_days: int = 30) -> None:
        """Remove log entries older than *keep_days* days (rewrites file)."""
        entries = self.history(hours=keep_days * 24)
        try:
            with self._lock:
                with open(self._file, "w", encoding="utf-8") as f:
                    for r in entries:
                        f.write(json.dumps(r.to_dict()) + "\n")
        except OSError as exc:
            logging.error("Log rotation failed: %s", exc)


# ---------------------------------------------------------------------------
# Reporter
# ---------------------------------------------------------------------------

_STATUS_EMOJI = {"UP": "✅", "SLOW": "⚠️", "DOWN": "❌", "ERROR": "⚠️",
                 "REDIRECT": "↩️", "UNKNOWN": "❓"}
_STATUS_PLAIN = {"UP": "[OK]", "SLOW": "[SLOW]", "DOWN": "[DOWN]",
                 "ERROR": "[ERR]", "REDIRECT": "[RDR]", "UNKNOWN": "[?]"}


def _status_symbol(status: str) -> str:
    if platform.system() == "Windows":
        return _STATUS_PLAIN.get(status, "[?]")
    return _STATUS_EMOJI.get(status, "❓")


class Reporter:
    """Generate human-readable uptime reports from stored history."""

    def __init__(self, logger: StatusLogger):
        self._logger = logger

    def generate(self, hours: int = 24) -> str:
        history = self._logger.history(hours)
        if not history:
            return f"No monitoring data for the past {hours} hours."

        # Group by URL
        by_url: Dict[str, List[CheckResult]] = {}
        for r in history:
            by_url.setdefault(r.url, []).append(r)

        lines = [
            "=" * 58,
            f"  Website Monitor Report — Last {hours}h",
            f"  Generated : {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC",
            f"  Sites     : {len(by_url)}",
            "=" * 58,
        ]

        for url, checks in by_url.items():
            total = len(checks)
            counts = {"UP": 0, "DOWN": 0, "SLOW": 0, "ERROR": 0,
                      "REDIRECT": 0, "UNKNOWN": 0}
            times: List[int] = []
            for c in checks:
                counts[c.status] = counts.get(c.status, 0) + 1
                if c.response_time_ms > 0:
                    times.append(c.response_time_ms)

            healthy = counts["UP"] + counts["SLOW"] + counts["REDIRECT"]
            uptime = (healthy / total * 100) if total else 0

            lines += [
                "",
                f"  {url}",
                "  " + "-" * 54,
                f"  Uptime         : {uptime:.2f}%",
                f"  Total checks   : {total}",
                f"  UP {counts['UP']:>4}  |  DOWN {counts['DOWN']:>4}  "
                f"|  SLOW {counts['SLOW']:>4}  |  ERR {counts['ERROR']:>4}",
            ]
            if times:
                avg = sum(times) / len(times)
                lines.append(
                    f"  Response (avg) : {avg:.0f}ms  "
                    f"min={min(times)}ms  max={max(times)}ms"
                )

            # SSL
            ssl_values = [c.ssl_days_remaining for c in checks
                          if c.ssl_days_remaining is not None]
            if ssl_values:
                latest_ssl = ssl_values[-1]
                warn = "  ⚠️  EXPIRES SOON" if latest_ssl < 14 else ""
                lines.append(f"  SSL expiry     : {latest_ssl} days remaining{warn}")

        lines += ["", "=" * 58, ""]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main monitor orchestrator
# ---------------------------------------------------------------------------

class WebsiteMonitor:
    """Top-level orchestrator: checks → logs → alerts → displays."""

    def __init__(self, config: Config):
        self.config = config
        self.checker = WebsiteChecker(config)
        self.alerts = AlertManager(config)
        self.status_logger = StatusLogger(config.log_dir)
        self.reporter = Reporter(self.status_logger)
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------

    def _display(self, result: CheckResult) -> None:
        sym = _status_symbol(result.status)
        ssl_info = ""
        if result.ssl_days_remaining is not None:
            ssl_info = f"  [SSL: {result.ssl_days_remaining}d]"
        kw_info = ""
        if result.keyword_found is not None:
            kw_info = (
                "  [KW: ✓]"
                if result.keyword_found
                else "  [KW: ✗ NOT FOUND]"
            )
        print(f"  {sym}  {result.url}")
        print(
            f"       {result.status}  HTTP {result.http_code}  "
            f"{result.response_time_ms}ms{ssl_info}{kw_info}"
        )
        print(f"       {result.message}")

    # ------------------------------------------------------------------

    def check_all(self, *, quiet: bool = False) -> List[CheckResult]:
        if not self.config.websites:
            print("⚠️  No websites configured. Edit monitor_config.json.")
            return []

        if not quiet:
            print()
            print(f"  {'─'*54}")
            print(f"  Website Monitor  {datetime.now():%Y-%m-%d %H:%M:%S}")
            print(f"  {'─'*54}")

        results = self.checker.check_all(self.config.websites)

        for result in results:
            self.status_logger.log(result)
            if not quiet:
                self._display(result)
            self.alerts.send_alerts(result)

        if not quiet:
            up = sum(1 for r in results if r.is_healthy())
            print()
            print(f"  {up}/{len(results)} healthy")
            print(f"  {'─'*54}")
            print()

        return results

    # ------------------------------------------------------------------

    def monitor_continuous(self) -> None:
        interval = self.config.get("check_interval", 300)
        print(f"\n  Continuous monitoring started (interval: {interval}s)")
        print("  Press Ctrl+C to stop.\n")

        # Graceful shutdown on SIGTERM
        def _handle_sigterm(*_):
            self._stop_event.set()

        signal.signal(signal.SIGTERM, _handle_sigterm)

        try:
            while not self._stop_event.is_set():
                self.check_all()
                # Sleep in small increments so Ctrl+C is responsive
                for _ in range(interval * 10):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            print("\n  Monitoring stopped.")
            logging.info("Continuous monitoring stopped")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="monitor",
        description="Cross-Platform Website Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python monitor.py check\n"
            "  python monitor.py monitor\n"
            "  python monitor.py report --hours 168\n"
            "  python monitor.py test\n"
            "  python monitor.py config\n"
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--config", metavar="PATH", help="Path to config JSON file"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Debug logging"
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    sub.add_parser("check", help="Run a single check on all configured sites")

    sub.add_parser("monitor", help="Run continuous monitoring (Ctrl+C to stop)")

    rep = sub.add_parser("report", help="Generate an uptime report")
    rep.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Lookback window in hours (default: 24)",
    )
    rep.add_argument(
        "--out", metavar="FILE", help="Write report to file instead of stdout"
    )

    sub.add_parser("test", help="Validate config and run a connectivity test")

    sub.add_parser("config", help="Print resolved configuration (secrets redacted)")

    rotate = sub.add_parser("rotate", help="Prune old log entries")
    rotate.add_argument(
        "--keep-days",
        type=int,
        default=30,
        metavar="N",
        help="Retain entries from last N days (default: 30)",
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    # Config
    config_path = Path(args.config) if getattr(args, "config", None) else None
    try:
        config = Config(config_path)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.log_dir, verbose=getattr(args, "verbose", False))
    monitor = WebsiteMonitor(config)

    # Dispatch
    cmd = args.command

    if cmd == "check":
        results = monitor.check_all()
        # Exit 1 if any site is down
        return 0 if all(r.is_healthy() for r in results) else 1

    elif cmd == "monitor":
        monitor.monitor_continuous()
        return 0

    elif cmd == "report":
        report = monitor.reporter.generate(args.hours)
        out = getattr(args, "out", None)
        if out:
            Path(out).write_text(report, encoding="utf-8")
            print(f"Report written to {out}")
        else:
            print(report)
        return 0

    elif cmd == "test":
        print(f"\n  Website Monitor v{__version__}")
        print(f"  Python  : {sys.version.split()[0]}  ({platform.system()})")
        print(f"  Config  : {config.config_path}")
        print(f"  Log dir : {config.log_dir}")
        print(f"  Sites   : {len(config.websites)}")
        for url in config.websites:
            print(f"    • {url}")
        print()
        results = monitor.check_all()
        return 0 if all(r.is_healthy() for r in results) else 1

    elif cmd == "config":
        print(repr(config))
        return 0

    elif cmd == "rotate":
        monitor.status_logger.rotate(args.keep_days)
        print(f"Log rotated — entries older than {args.keep_days} days removed.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
