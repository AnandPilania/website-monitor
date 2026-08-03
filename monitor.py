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
import socket
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
from urllib.parse import urlparse, parse_qs, urlencode

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__ = "0.0.5"

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
    domain_days_remaining: Optional[int] = None
    missing_security_headers: Optional[List[str]] = None
    dns_records: Optional[Dict[str, List[str]]] = None

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
    "MONITOR_NTFY_TOPIC":            "alerts.ntfy.topic",
    "MONITOR_PUSHOVER_USER_KEY":     "alerts.pushover.user_key",
    "MONITOR_PUSHOVER_API_TOKEN":    "alerts.pushover.api_token",
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
    "request_overrides": {},       # {"https://example.com": {"method": "POST",
                                   #   "headers": {"X-Api-Key": "..."},
                                   #   "body": "{\"ping\": true}"}}
    "security_headers_check": False,   # verify common security headers are present
    "domain_expiry_check": False,      # verify domain registration expiry via RDAP
    "domain_expiry_warn_days": 30,     # warn threshold for domain expiry
    "ssl_expiry_warn_days": 14,        # warn threshold for SSL cert expiry
    "dns_checks": {},              # {"example.com": ["A", "MX", "TXT"]}
    "tcp_checks": {},               # {"name": {"host": "db.example.com", "port": 5432}}
    "push_monitors": {},            # {"id": {"name": "Nightly Backup", "timeout_seconds": 90000}}
    "maintenance_windows": [],      # [{"start": "2026-08-01T02:00:00Z", "end": "...", "urls": [...]}]
                                    # omit "urls" (or use []) to apply to all sites
    "prometheus": {
        "enabled": False,
        "port": 9877,
    },
    "dashboard": {
        "enabled": False,
        "port": 8877,
    },
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
        "ntfy": {
            "enabled": False,
            "server": "https://ntfy.sh",
            "topic": "",           # prefer MONITOR_NTFY_TOPIC env var
        },
        "pushover": {
            "enabled": False,
            "user_key": "",        # prefer MONITOR_PUSHOVER_USER_KEY env var
            "api_token": "",       # prefer MONITOR_PUSHOVER_API_TOKEN env var
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
        for path in (
            ("alerts", "slack", "webhook_url"),
            ("alerts", "webhook", "url"),
            ("alerts", "ntfy", "topic"),
            ("alerts", "pushover", "user_key"),
            ("alerts", "pushover", "api_token"),
        ):
            node = safe
            for key in path[:-1]:
                node = node.setdefault(key, {})
            if node.get(path[-1]):
                node[path[-1]] = "***"
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
# Security headers
# ---------------------------------------------------------------------------

# Header name -> informational note used in alert/report messages.
RECOMMENDED_SECURITY_HEADERS: Dict[str, str] = {
    "Strict-Transport-Security": "enforces HTTPS (HSTS)",
    "X-Content-Type-Options": "prevents MIME-sniffing",
    "X-Frame-Options": "mitigates clickjacking",
    "Content-Security-Policy": "restricts allowed content sources",
    "Referrer-Policy": "controls referrer leakage",
}


def _missing_security_headers(headers) -> List[str]:
    """Return the subset of RECOMMENDED_SECURITY_HEADERS not present in *headers*."""
    present = {k.lower() for k in headers.keys()}
    return [
        name for name in RECOMMENDED_SECURITY_HEADERS
        if name.lower() not in present
    ]


# ---------------------------------------------------------------------------
# DNS records (stdlib-only: shells out to the OS resolver, no dnspython needed)
# ---------------------------------------------------------------------------

def _resolve_dns(hostname: str, record_types: List[str], timeout: int = 5) -> Dict[str, List[str]]:
    """Resolve the requested record types for *hostname*.

    Uses `nslookup` (present on Windows/macOS/Linux) rather than a third-party
    DNS library, keeping the tool dependency-free. A/AAAA fall back to the
    stdlib `socket` resolver if `nslookup` is unavailable.
    """
    results: Dict[str, List[str]] = {}
    for rtype in record_types:
        rtype = rtype.upper()
        try:
            if rtype in ("A", "AAAA") and _which("nslookup") is None:
                family = socket.AF_INET if rtype == "A" else socket.AF_INET6
                infos = socket.getaddrinfo(hostname, None, family)
                results[rtype] = sorted({info[4][0] for info in infos})
                continue

            proc = subprocess.run(
                ["nslookup", "-type=" + rtype, hostname],
                capture_output=True, text=True, timeout=timeout,
            )
            results[rtype] = _parse_nslookup(proc.stdout, rtype)
        except Exception as exc:  # noqa: BLE001
            results[rtype] = [f"error: {exc}"]
    return results


def _which(cmd: str) -> Optional[str]:
    import shutil
    return shutil.which(cmd)


def _parse_nslookup(output: str, rtype: str) -> List[str]:
    """Best-effort parse of nslookup's plain-text output."""
    values: List[str] = []
    for line in output.splitlines():
        line = line.strip()
        if rtype in ("A", "AAAA") and line.startswith("Address:"):
            addr = line.split(":", 1)[1].strip()
            if addr and "#" not in addr:  # skip the resolver's own "Address: x#53"
                values.append(addr)
        elif rtype == "MX" and "mail exchanger" in line:
            values.append(line.split("=", 1)[-1].strip())
        elif rtype == "TXT" and "text =" in line:
            values.append(line.split("text =", 1)[-1].strip().strip('"'))
        elif rtype == "CNAME" and "canonical name" in line:
            values.append(line.split("=", 1)[-1].strip())
        elif rtype == "NS" and "nameserver" in line:
            values.append(line.split("=", 1)[-1].strip())
    return values


# ---------------------------------------------------------------------------
# Domain (registration) expiry — via public RDAP, no whois library required
# ---------------------------------------------------------------------------

_RDAP_BOOTSTRAP = "https://rdap.org/domain/"


def _domain_days_remaining(hostname: str, timeout: int = 5) -> Optional[int]:
    """Return days until domain registration expiry using RDAP, or None."""
    # Reduce to registrable domain (best-effort: last two labels).
    labels = hostname.split(".")
    domain = ".".join(labels[-2:]) if len(labels) >= 2 else hostname
    try:
        req = urllib.request.Request(
            _RDAP_BOOTSTRAP + domain,
            headers={"User-Agent": f"WebsiteMonitor/{__version__}", "Accept": "application/rdap+json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        for event in data.get("events", []):
            if event.get("eventAction") == "expiration":
                expiry = datetime.strptime(
                    event["eventDate"].split(".")[0].replace("Z", ""),
                    "%Y-%m-%dT%H:%M:%S",
                )
                return (expiry - datetime.utcnow()).days
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pluggable post-fetch check registry
# ---------------------------------------------------------------------------
#
# Each "extra check" (keyword matching, security headers, SSL/domain expiry,
# DNS) is a small function registered here rather than hardcoded inline in
# WebsiteChecker.check_once(). To add a new check:
#
#   1. Write a function matching PostFetchCheck's signature below.
#   2. Decide whether it needs a live HTTP response (`requires_response=True`,
#      e.g. keyword/header checks) or just the URL (`requires_response=False`,
#      e.g. SSL/DNS/domain checks that open their own connection and should
#      still run even if the HTTP fetch itself failed).
#   3. Append it to POST_FETCH_CHECKS.
#
# Each function mutates `result` in place; return value is ignored.

@dataclass
class CheckContext:
    """Everything a post-fetch check might need, gathered in one place."""
    url: str
    parsed: object  # urllib.parse.ParseResult
    config: "WebsiteChecker"
    resp_headers: object = None   # email.message.Message-like, only if fetch succeeded
    resp_body: bytes = b""        # only if fetch succeeded


class PostFetchCheck:
    """A single registrable check: name, whether it needs a live response, and the fn."""

    def __init__(self, name: str, fn, *, requires_response: bool):
        self.name = name
        self.fn = fn
        self.requires_response = requires_response

    def run(self, result: "CheckResult", ctx: CheckContext) -> None:
        if self.requires_response and result.status not in ("UP", "SLOW"):
            return  # no usable response body/headers to check
        try:
            self.fn(result, ctx)
        except Exception as exc:  # noqa: BLE001
            logging.debug("Post-fetch check '%s' failed for %s: %s", self.name, ctx.url, exc)


def _check_keyword(result: "CheckResult", ctx: CheckContext) -> None:
    kw = ctx.config.keyword_checks.get(ctx.url)
    if kw:
        result.keyword_found = kw.encode() in ctx.resp_body


def _check_security_headers(result: "CheckResult", ctx: CheckContext) -> None:
    if ctx.config.security_headers_check:
        result.missing_security_headers = _missing_security_headers(ctx.resp_headers)


def _check_ssl_expiry(result: "CheckResult", ctx: CheckContext) -> None:
    if ctx.parsed.scheme == "https":
        result.ssl_days_remaining = _ssl_days_remaining(
            ctx.parsed.hostname, ctx.parsed.port or 443, ctx.config.timeout
        )


def _check_domain_expiry(result: "CheckResult", ctx: CheckContext) -> None:
    if ctx.config.domain_expiry_check and ctx.parsed.hostname:
        result.domain_days_remaining = _domain_days_remaining(
            ctx.parsed.hostname, ctx.config.timeout
        )


def _check_dns_records(result: "CheckResult", ctx: CheckContext) -> None:
    if ctx.parsed.hostname and ctx.parsed.hostname in ctx.config.dns_checks:
        result.dns_records = _resolve_dns(
            ctx.parsed.hostname, ctx.config.dns_checks[ctx.parsed.hostname], ctx.config.timeout
        )


# Order matters only for readability/logging; each check is independent.
POST_FETCH_CHECKS: List[PostFetchCheck] = [
    PostFetchCheck("keyword", _check_keyword, requires_response=True),
    PostFetchCheck("security_headers", _check_security_headers, requires_response=True),
    PostFetchCheck("ssl_expiry", _check_ssl_expiry, requires_response=False),
    PostFetchCheck("domain_expiry", _check_domain_expiry, requires_response=False),
    PostFetchCheck("dns_records", _check_dns_records, requires_response=False),
]


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
        self.request_overrides: Dict[str, Dict] = config.get("request_overrides") or {}
        self.security_headers_check: bool = config.get("security_headers_check", False)
        self.domain_expiry_check: bool = config.get("domain_expiry_check", False)
        self.dns_checks: Dict[str, List[str]] = config.get("dns_checks") or {}

        self._ssl_ctx = ssl.create_default_context()

    # ------------------------------------------------------------------

    def check_once(self, url: str) -> CheckResult:
        """Single HTTP check — no retries."""
        result = CheckResult(url=url, status="UNKNOWN", http_code=0,
                             response_time_ms=0, message="")

        override = self.request_overrides.get(url) or {}
        method = override.get("method", "GET").upper()
        extra_headers = override.get("headers") or {}
        body_str = override.get("body")
        data = body_str.encode() if body_str is not None else None

        headers = {"User-Agent": f"WebsiteMonitor/{__version__}"}
        headers.update(extra_headers)
        if data is not None and "Content-Type" not in extra_headers:
            headers["Content-Type"] = "application/json"

        resp_headers = None
        resp_body = b""

        try:
            req = urllib.request.Request(
                url, data=data, headers=headers, method=method
            )
            t0 = time.monotonic()
            with urllib.request.urlopen(
                req, timeout=self.timeout, context=self._ssl_ctx
            ) as resp:
                resp_body = resp.read()
                elapsed = int((time.monotonic() - t0) * 1000)
                code = resp.status
                resp_headers = resp.headers

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

        # Run every registered post-fetch check against this result.
        ctx = CheckContext(
            url=url, parsed=urlparse(url), config=self,
            resp_headers=resp_headers, resp_body=resp_body,
        )
        for check in POST_FETCH_CHECKS:
            check.run(result, ctx)

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

    # ------------------------------------------------------------------
    # TCP port checks (non-HTTP: databases, mail servers, game servers, etc.)
    # ------------------------------------------------------------------

    def check_tcp_once(self, name: str, host: str, port: int) -> CheckResult:
        """Attempt a raw TCP connection. Uses a synthetic tcp://host:port
        'url' so results flow through the same CheckResult/logging/alerting/
        reporting pipeline as HTTP checks."""
        pseudo_url = f"tcp://{host}:{port}"
        result = CheckResult(url=pseudo_url, status="UNKNOWN", http_code=0,
                             response_time_ms=0, message="")
        t0 = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=self.timeout):
                elapsed = int((time.monotonic() - t0) * 1000)
                result.response_time_ms = elapsed
                if elapsed > self.max_response_time:
                    result.status = "SLOW"
                    result.message = f"{name}: connected but slow ({elapsed}ms)"
                else:
                    result.status = "UP"
                    result.message = f"{name}: TCP connect OK ({elapsed}ms)"
        except (socket.timeout, TimeoutError):
            result.status = "DOWN"
            result.message = f"{name}: connection timed out after {self.timeout}s"
        except OSError as exc:
            result.status = "DOWN"
            result.message = f"{name}: connection failed ({exc})"
        return result

    def check_tcp_with_retry(self, name: str, host: str, port: int) -> CheckResult:
        max_retries: int = self.config.get("max_retries", 3)
        delay: float = self.config.get("retry_delay", 2)
        backoff: float = self.config.get("retry_backoff", 2.0)

        result = CheckResult(url=f"tcp://{host}:{port}", status="UNKNOWN",
                             http_code=0, response_time_ms=0, message="")
        for attempt in range(1, max_retries + 1):
            result = self.check_tcp_once(name, host, port)
            if result.is_healthy():
                return result
            if attempt < max_retries:
                time.sleep(delay)
                delay *= backoff
        return result

    def check_all_tcp(self, tcp_checks: Dict[str, Dict]) -> List[CheckResult]:
        """tcp_checks: {"name": {"host": ..., "port": ...}}"""
        if not tcp_checks:
            return []
        workers = min(self.config.get("concurrent_checks", 5), len(tcp_checks))
        results: List[CheckResult] = []
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="tcp-checker"
        ) as pool:
            futures = {
                pool.submit(self.check_tcp_with_retry, name, spec["host"], spec["port"]): name
                for name, spec in tcp_checks.items()
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    name = futures[future]
                    results.append(CheckResult(url=f"tcp://{name}", status="UNKNOWN",
                                               http_code=0, response_time_ms=0, message=str(exc)))
        return results


# ---------------------------------------------------------------------------
# Alert manager
# ---------------------------------------------------------------------------

class AlertManager:
    """Multi-channel alerting (desktop, email, webhook, Slack)."""

    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()  # prevent alert storms
        self.ssl_warn_days = config.get("ssl_expiry_warn_days", 14)
        self.domain_warn_days = config.get("domain_expiry_warn_days", 30)

    def _needs_alert(self, result: CheckResult) -> bool:
        if result.status in ("DOWN", "ERROR"):
            return True
        if (result.ssl_days_remaining is not None
                and result.ssl_days_remaining < self.ssl_warn_days):
            return True
        if (result.domain_days_remaining is not None
                and result.domain_days_remaining < self.domain_warn_days):
            return True
        if result.missing_security_headers:
            return True
        return False

    def send_alerts(self, result: CheckResult) -> None:
        if not self._needs_alert(result):
            return
        with self._lock:
            self._desktop(result)
            self._email(result)
            self._webhook(result)
            self._slack(result)
            self._ntfy(result)
            self._pushover(result)

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
            domain_line = ""
            if result.domain_days_remaining is not None:
                domain_line = f"Domain Exp. : {result.domain_days_remaining} days remaining\n"
            headers_line = ""
            if result.missing_security_headers:
                headers_line = (
                    "Missing Hdrs: " + ", ".join(result.missing_security_headers) + "\n"
                )
            body = (
                f"Website Monitoring Alert\n"
                f"{'='*40}\n"
                f"URL         : {result.url}\n"
                f"Status      : {result.status}\n"
                f"HTTP Code   : {result.http_code}\n"
                f"Response    : {result.response_time_ms}ms\n"
                f"{ssl_line}"
                f"{domain_line}"
                f"{headers_line}"
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
            if result.domain_days_remaining is not None:
                fields.append({
                    "title": "Domain Expiry",
                    "value": f"{result.domain_days_remaining} days",
                    "short": True,
                })
            if result.missing_security_headers:
                fields.append({
                    "title": "Missing Security Headers",
                    "value": ", ".join(result.missing_security_headers),
                    "short": False,
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

    # ------------------------------------------------------------------
    # ntfy.sh — simple pub/sub push notifications, no account required
    # ------------------------------------------------------------------

    def _ntfy(self, result: CheckResult) -> None:
        cfg = self.config.get_nested("alerts", "ntfy") or {}
        if not cfg.get("enabled") or not cfg.get("topic"):
            return
        try:
            server = cfg.get("server", "https://ntfy.sh").rstrip("/")
            url = f"{server}/{cfg['topic']}"
            priority = "urgent" if result.status == "DOWN" else "default"
            body = result.message.encode("utf-8")
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={
                    "Title": f"{result.status}: {result.url}",
                    "Priority": priority,
                    "Tags": "warning" if result.status != "DOWN" else "rotating_light",
                },
            )
            urllib.request.urlopen(req, timeout=5)
            logging.info("ntfy alert sent")
        except Exception as exc:  # noqa: BLE001
            logging.error("ntfy alert failed: %s", exc)

    # ------------------------------------------------------------------
    # Pushover — mobile push notifications
    # ------------------------------------------------------------------

    def _pushover(self, result: CheckResult) -> None:
        cfg = self.config.get_nested("alerts", "pushover") or {}
        if not cfg.get("enabled") or not cfg.get("user_key") or not cfg.get("api_token"):
            return
        try:
            payload = urlencode({
                "token": cfg["api_token"],
                "user": cfg["user_key"],
                "title": f"Website Monitor: {result.status}",
                "message": f"{result.url}\n{result.message}",
                "priority": 1 if result.status == "DOWN" else 0,
            }).encode()
            req = urllib.request.Request(
                "https://api.pushover.net/1/messages.json",
                data=payload, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            logging.info("Pushover alert sent")
        except Exception as exc:  # noqa: BLE001
            logging.error("Pushover alert failed: %s", exc)


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
    """Generate uptime statistics and human-readable reports from stored history."""

    def __init__(self, logger: StatusLogger):
        self._logger = logger

    def compute_stats(self, hours: int = 24) -> Dict[str, Dict]:
        """Return per-URL stats as plain dicts (JSON-serializable).

        Shared by both the text report (`generate`) and the dashboard API,
        so the two never drift out of sync.
        """
        history = self._logger.history(hours)
        by_url: Dict[str, List[CheckResult]] = {}
        for r in history:
            by_url.setdefault(r.url, []).append(r)

        stats: Dict[str, Dict] = {}
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
            uptime_pct = (healthy / total * 100) if total else 0.0

            latest = max(checks, key=lambda c: c.timestamp)
            ssl_checks = [c for c in checks if c.ssl_days_remaining is not None]
            domain_checks = [c for c in checks if c.domain_days_remaining is not None]
            latest_ssl = max(ssl_checks, key=lambda c: c.timestamp) if ssl_checks else None
            latest_domain = max(domain_checks, key=lambda c: c.timestamp) if domain_checks else None

            stats[url] = {
                "url": url,
                "uptime_pct": round(uptime_pct, 2),
                "total_checks": total,
                "counts": counts,
                "avg_response_ms": round(sum(times) / len(times), 1) if times else None,
                "min_response_ms": min(times) if times else None,
                "max_response_ms": max(times) if times else None,
                "latest_status": latest.status,
                "latest_http_code": latest.http_code,
                "latest_message": latest.message,
                "latest_timestamp": latest.timestamp,
                "ssl_days_remaining": latest_ssl.ssl_days_remaining if latest_ssl else None,
                "domain_days_remaining": latest_domain.domain_days_remaining if latest_domain else None,
                "missing_security_headers": latest.missing_security_headers,
            }
        return stats

    def generate(self, hours: int = 24) -> str:
        stats = self.compute_stats(hours)
        if not stats:
            return f"No monitoring data for the past {hours} hours."

        lines = [
            "=" * 58,
            f"  Website Monitor Report — Last {hours}h",
            f"  Generated : {datetime.utcnow():%Y-%m-%d %H:%M:%S} UTC",
            f"  Sites     : {len(stats)}",
            "=" * 58,
        ]

        for url, s in stats.items():
            c = s["counts"]
            lines += [
                "",
                f"  {url}",
                "  " + "-" * 54,
                f"  Uptime         : {s['uptime_pct']:.2f}%",
                f"  Total checks   : {s['total_checks']}",
                f"  UP {c['UP']:>4}  |  DOWN {c['DOWN']:>4}  "
                f"|  SLOW {c['SLOW']:>4}  |  ERR {c['ERROR']:>4}",
            ]
            if s["avg_response_ms"] is not None:
                lines.append(
                    f"  Response (avg) : {s['avg_response_ms']:.0f}ms  "
                    f"min={s['min_response_ms']}ms  max={s['max_response_ms']}ms"
                )
            if s["ssl_days_remaining"] is not None:
                warn = "  ⚠️  EXPIRES SOON" if s["ssl_days_remaining"] < 14 else ""
                lines.append(f"  SSL expiry     : {s['ssl_days_remaining']} days remaining{warn}")

        lines += ["", "=" * 58, ""]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prometheus metrics export (stdlib-only text exposition format)
# ---------------------------------------------------------------------------

_STATUS_TO_UP_VALUE = {"UP": 1, "SLOW": 1, "REDIRECT": 1, "DOWN": 0, "ERROR": 0, "UNKNOWN": 0}


def render_prometheus_metrics(results: List[CheckResult]) -> str:
    """Render the latest CheckResults as Prometheus text-exposition format."""
    lines = [
        "# HELP website_monitor_up Whether the last check considered the site healthy (1) or not (0).",
        "# TYPE website_monitor_up gauge",
    ]
    for r in results:
        lines.append(
            f'website_monitor_up{{url="{r.url}"}} {_STATUS_TO_UP_VALUE.get(r.status, 0)}'
        )

    lines += [
        "# HELP website_monitor_response_time_ms Response time of the last check in milliseconds.",
        "# TYPE website_monitor_response_time_ms gauge",
    ]
    for r in results:
        lines.append(f'website_monitor_response_time_ms{{url="{r.url}"}} {r.response_time_ms}')

    lines += [
        "# HELP website_monitor_http_code HTTP status code of the last check.",
        "# TYPE website_monitor_http_code gauge",
    ]
    for r in results:
        lines.append(f'website_monitor_http_code{{url="{r.url}"}} {r.http_code}')

    ssl_results = [r for r in results if r.ssl_days_remaining is not None]
    if ssl_results:
        lines += [
            "# HELP website_monitor_ssl_days_remaining Days until the TLS certificate expires.",
            "# TYPE website_monitor_ssl_days_remaining gauge",
        ]
        for r in ssl_results:
            lines.append(
                f'website_monitor_ssl_days_remaining{{url="{r.url}"}} {r.ssl_days_remaining}'
            )

    domain_results = [r for r in results if r.domain_days_remaining is not None]
    if domain_results:
        lines += [
            "# HELP website_monitor_domain_days_remaining Days until domain registration expires.",
            "# TYPE website_monitor_domain_days_remaining gauge",
        ]
        for r in domain_results:
            lines.append(
                f'website_monitor_domain_days_remaining{{url="{r.url}"}} {r.domain_days_remaining}'
            )

    return "\n".join(lines) + "\n"


class MetricsServer:
    """Minimal stdlib HTTP server exposing the latest results as /metrics."""

    def __init__(self, monitor: "WebsiteMonitor", port: int = 9877):
        self._monitor = monitor
        self._port = port
        self._latest: List[CheckResult] = []
        self._lock = threading.Lock()

    def update(self, results: List[CheckResult]) -> None:
        with self._lock:
            self._latest = results

    def _make_handler(self):
        server_self = self

        class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path != "/metrics":
                    self.send_response(404)
                    self.end_headers()
                    return
                with server_self._lock:
                    body = render_prometheus_metrics(server_self._latest).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):  # noqa: A002
                logging.debug("metrics server: " + fmt, *args)

        return Handler

    def serve_forever(self) -> None:
        import http.server
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self._port), self._make_handler())
        logging.info("Prometheus metrics server listening on :%d/metrics", self._port)
        httpd.serve_forever()


# ---------------------------------------------------------------------------
# Web dashboard + REST API (stdlib-only: http.server, no framework/dependency)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Push / heartbeat monitoring
# ---------------------------------------------------------------------------
#
# Unlike every other check in this file, push monitors are *passive*: instead
# of this tool reaching out to a URL, some external process (a cron job, a
# backup script, a batch worker) calls back in to say "I'm alive." If no
# heartbeat arrives within `timeout_seconds`, the monitor is considered DOWN.
# This covers anything that can't be polled from the outside.

class PushMonitorRegistry:
    """Tracks configured push monitors and their last-seen heartbeat.

    State is persisted to a small JSON file so heartbeats survive restarts
    (otherwise every restart would look like every push monitor just missed
    its window).
    """

    def __init__(self, push_monitors: Dict[str, Dict], state_path: Path):
        self.monitors = push_monitors  # {id: {"name": ..., "timeout_seconds": ...}}
        self.state_path = state_path
        self._lock = threading.Lock()
        self._state: Dict[str, Dict] = self._load()

    def _load(self) -> Dict[str, Dict]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                logging.warning("Could not read push monitor state; starting fresh")
        return {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._state, indent=2))
        except OSError as exc:
            logging.warning("Could not persist push monitor state: %s", exc)

    def record_heartbeat(self, push_id: str, message: str = "") -> bool:
        """Record a heartbeat for push_id. Returns False if push_id is unknown."""
        if push_id not in self.monitors:
            return False
        with self._lock:
            self._state[push_id] = {
                "last_seen": datetime.utcnow().isoformat() + "Z",
                "message": message or "OK",
            }
            self._save()
        return True

    def check_all(self) -> List[CheckResult]:
        """Return one CheckResult per configured push monitor, DOWN if the
        heartbeat has expired or never arrived."""
        results: List[CheckResult] = []
        now = datetime.utcnow()
        with self._lock:
            for push_id, spec in self.monitors.items():
                name = spec.get("name", push_id)
                timeout_s = spec.get("timeout_seconds", 3600)
                pseudo_url = f"push://{push_id}"
                seen = self._state.get(push_id)

                if seen is None:
                    results.append(CheckResult(
                        url=pseudo_url, status="UNKNOWN", http_code=0,
                        response_time_ms=0,
                        message=f"{name}: no heartbeat received yet",
                    ))
                    continue

                last_seen = datetime.fromisoformat(seen["last_seen"].replace("Z", ""))
                age_s = (now - last_seen).total_seconds()
                if age_s > timeout_s:
                    results.append(CheckResult(
                        url=pseudo_url, status="DOWN", http_code=0,
                        response_time_ms=0,
                        message=f"{name}: no heartbeat for {int(age_s)}s "
                                f"(timeout {timeout_s}s)",
                    ))
                else:
                    results.append(CheckResult(
                        url=pseudo_url, status="UP", http_code=0,
                        response_time_ms=0,
                        message=f"{name}: {seen.get('message', 'OK')} "
                                f"({int(age_s)}s ago)",
                    ))
        return results


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website Monitor</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 2rem; background: #0f1115; color: #e6e6e6; }
  h1 { font-size: 1.4rem; margin: 0 0 1.25rem; font-weight: 600; }
  .meta { color: #8a8f98; font-size: 0.85rem; margin-bottom: 1.5rem; }
  .grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
  .card { background: #181b21; border: 1px solid #2a2e37; border-radius: 10px; padding: 1rem 1.2rem; }
  .card h2 { font-size: 0.95rem; margin: 0 0 0.6rem; word-break: break-all; font-weight: 600; }
  .row { display: flex; justify-content: space-between; font-size: 0.85rem;
         color: #b7bcc7; padding: 0.15rem 0; }
  .row b { color: #e6e6e6; font-weight: 500; }
  .badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
           font-size: 0.75rem; font-weight: 600; }
  .up { background: #113322; color: #4ade80; }
  .slow { background: #3a2f10; color: #fbbf24; }
  .down, .error { background: #3a1414; color: #f87171; }
  .redirect { background: #142338; color: #60a5fa; }
  .unknown { background: #262a33; color: #9ca3af; }
  .warn-text { color: #fbbf24; }
  .empty { color: #8a8f98; padding: 2rem 0; }
</style>
</head>
<body>
  <h1>Website Monitor</h1>
  <div class="meta" id="meta">Loading…</div>
  <div class="grid" id="grid"></div>

<script>
const badgeClass = s => ({UP:"up", SLOW:"slow", DOWN:"down", ERROR:"error",
                           REDIRECT:"redirect"}[s] || "unknown");

async function refresh() {
  try {
    const res = await fetch("/api/uptime?hours=24");
    const data = await res.json();
    const urls = Object.keys(data);
    document.getElementById("meta").textContent =
      urls.length ? `${urls.length} site(s) — last 24h — updates every 15s`
                  : "No monitoring data yet";
    const grid = document.getElementById("grid");
    if (!urls.length) {
      grid.innerHTML = '<div class="empty">No checks recorded yet. Run `python monitor.py check` or `monitor` first.</div>';
      return;
    }
    grid.innerHTML = urls.map(u => {
      const s = data[u];
      const ssl = s.ssl_days_remaining != null
        ? `<div class="row">SSL expiry <b class="${s.ssl_days_remaining < 14 ? 'warn-text' : ''}">${s.ssl_days_remaining}d</b></div>` : "";
      const domain = s.domain_days_remaining != null
        ? `<div class="row">Domain expiry <b class="${s.domain_days_remaining < 30 ? 'warn-text' : ''}">${s.domain_days_remaining}d</b></div>` : "";
      const resp = s.avg_response_ms != null
        ? `<div class="row">Avg response <b>${Math.round(s.avg_response_ms)}ms</b></div>` : "";
      return `<div class="card">
        <h2>${u}</h2>
        <div class="row">Status <span class="badge ${badgeClass(s.latest_status)}">${s.latest_status}</span></div>
        <div class="row">Uptime (24h) <b>${s.uptime_pct.toFixed(2)}%</b></div>
        <div class="row">Checks <b>${s.total_checks}</b></div>
        ${resp}${ssl}${domain}
      </div>`;
    }).join("");
  } catch (e) {
    document.getElementById("meta").textContent = "Failed to load status: " + e;
  }
}
refresh();
setInterval(refresh, 15000);
</script>
</body>
</html>
"""


class DashboardServer:
    """Minimal stdlib HTTP server: a live status dashboard + JSON REST API.

    No framework, no third-party dependency — the page above polls the
    JSON endpoints with plain fetch(). Data comes straight from the same
    StatusLogger/Reporter used by the CLI, so the dashboard is always
    consistent with `report`/`metrics` output.
    """

    def __init__(self, reporter: "Reporter", logger: "StatusLogger", port: int = 8877,
                 push_registry: Optional["PushMonitorRegistry"] = None):
        self._reporter = reporter
        self._logger = logger
        self._port = port
        self._push_registry = push_registry

    def _make_handler(self):
        reporter = self._reporter
        logger_ = self._logger
        push_registry = self._push_registry

        class Handler(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
            def _json(self, payload, status: int = 200):
                body = json.dumps(payload, default=str).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _handle_push(self, push_id: str, message: str = ""):
                if push_registry is None:
                    self._json({"error": "push monitoring not enabled"}, status=404)
                    return
                ok = push_registry.record_heartbeat(push_id, message)
                if ok:
                    self._json({"status": "ok", "push_id": push_id})
                else:
                    self._json({"error": f"unknown push id: {push_id}"}, status=404)

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                hours = int(qs.get("hours", ["24"])[0])

                if parsed.path == "/":
                    body = _DASHBOARD_HTML.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                elif parsed.path == "/api/uptime":
                    self._json(reporter.compute_stats(hours))

                elif parsed.path == "/api/history":
                    results = logger_.history(hours)
                    self._json([r.to_dict() for r in results])

                elif parsed.path == "/api/status":
                    stats = reporter.compute_stats(hours=24)
                    overall_healthy = all(
                        s["latest_status"] in ("UP", "SLOW", "REDIRECT")
                        for s in stats.values()
                    ) if stats else None
                    self._json({"sites": stats, "healthy": overall_healthy})

                elif parsed.path.startswith("/api/push/"):
                    # GET is supported alongside POST since many cron/curl
                    # one-liners find a bare GET easier than a POST body.
                    push_id = parsed.path[len("/api/push/"):]
                    message = qs.get("msg", [""])[0]
                    self._handle_push(push_id, message)

                else:
                    self._json({"error": "not found"}, status=404)

            def do_POST(self):  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.startswith("/api/push/"):
                    push_id = parsed.path[len("/api/push/"):]
                    length = int(self.headers.get("Content-Length", 0))
                    body = self.rfile.read(length) if length else b""
                    message = body.decode(errors="replace").strip()
                    self._handle_push(push_id, message)
                else:
                    self._json({"error": "not found"}, status=404)

            def log_message(self, fmt, *args):  # noqa: A002
                logging.debug("dashboard server: " + fmt, *args)

        return Handler

    def serve_forever(self) -> None:
        import http.server
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self._port), self._make_handler())
        logging.info("Dashboard listening on http://0.0.0.0:%d/", self._port)
        httpd.serve_forever()


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

        push_monitors = config.get("push_monitors") or {}
        self.push_registry: Optional[PushMonitorRegistry] = None
        if push_monitors:
            state_path = config.log_dir / "push_state.json"
            self.push_registry = PushMonitorRegistry(push_monitors, state_path)

        self.metrics_server: Optional[MetricsServer] = None
        if config.get_nested("prometheus", "enabled", default=False):
            port = config.get_nested("prometheus", "port", default=9877)
            self.metrics_server = MetricsServer(self, port)
            threading.Thread(
                target=self.metrics_server.serve_forever, daemon=True
            ).start()

        self.dashboard_server: Optional[DashboardServer] = None
        if config.get_nested("dashboard", "enabled", default=False) or push_monitors:
            port = config.get_nested("dashboard", "port", default=8877)
            self.dashboard_server = DashboardServer(
                self.reporter, self.status_logger, port, push_registry=self.push_registry
            )
            threading.Thread(
                target=self.dashboard_server.serve_forever, daemon=True
            ).start()

    # ------------------------------------------------------------------

    def _in_maintenance(self, url: str) -> bool:
        """True if *url* currently falls inside a configured maintenance
        window. Checks still run and log during maintenance — only alerts
        are suppressed, matching how every comparable tool treats planned
        downtime."""
        windows = self.config.get("maintenance_windows") or []
        if not windows:
            return False
        now = datetime.utcnow()
        for w in windows:
            try:
                start = datetime.fromisoformat(w["start"].replace("Z", ""))
                end = datetime.fromisoformat(w["end"].replace("Z", ""))
            except (KeyError, ValueError):
                continue
            if not (start <= now <= end):
                continue
            applies_to = w.get("urls") or []
            if not applies_to or url in applies_to:
                return True
        return False

    def _display(self, result: CheckResult) -> None:
        sym = _status_symbol(result.status)
        ssl_info = ""
        if result.ssl_days_remaining is not None:
            ssl_info = f"  [SSL: {result.ssl_days_remaining}d]"
        domain_info = ""
        if result.domain_days_remaining is not None:
            domain_info = f"  [Domain: {result.domain_days_remaining}d]"
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
            f"{result.response_time_ms}ms{ssl_info}{domain_info}{kw_info}"
        )
        print(f"       {result.message}")
        if result.missing_security_headers:
            print(
                "       ⚠ Missing security headers: "
                + ", ".join(result.missing_security_headers)
            )
        if result.dns_records:
            for rtype, values in result.dns_records.items():
                print(f"       DNS {rtype}: {', '.join(values) if values else '(none)'}")

    # ------------------------------------------------------------------

    def check_all(self, *, quiet: bool = False) -> List[CheckResult]:
        tcp_checks = self.config.get("tcp_checks") or {}
        push_monitors = self.config.get("push_monitors") or {}
        if not self.config.websites and not tcp_checks and not push_monitors:
            print("⚠️  Nothing configured to check. Edit monitor_config.json.")
            return []

        if not quiet:
            print()
            print(f"  {'─'*54}")
            print(f"  Website Monitor  {datetime.now():%Y-%m-%d %H:%M:%S}")
            print(f"  {'─'*54}")

        results = self.checker.check_all(self.config.websites)
        results += self.checker.check_all_tcp(tcp_checks)
        if self.push_registry is not None:
            results += self.push_registry.check_all()

        for result in results:
            self.status_logger.log(result)
            if not quiet:
                self._display(result)
            if not self._in_maintenance(result.url):
                self.alerts.send_alerts(result)

        if self.metrics_server is not None:
            self.metrics_server.update(results)

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
            "  python monitor.py metrics\n"
            "  python monitor.py dashboard\n"
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

    sub.add_parser(
        "metrics",
        help="Run a single check and print results in Prometheus text format",
    )

    dashboard = sub.add_parser(
        "dashboard",
        help="Serve the live web dashboard + JSON API (Ctrl+C to stop)",
    )
    dashboard.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="Port to serve on (default: config's dashboard.port, or 8877)",
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

    elif cmd == "metrics":
        results = monitor.check_all(quiet=True)
        print(render_prometheus_metrics(results), end="")
        return 0 if all(r.is_healthy() for r in results) else 1

    elif cmd == "dashboard":
        port = args.port or config.get_nested("dashboard", "port", default=8877)
        server = DashboardServer(monitor.reporter, monitor.status_logger, port)
        print(f"Dashboard running at http://localhost:{port}/  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nDashboard stopped.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
