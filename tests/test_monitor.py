"""
Website Monitor — Test Suite
Run: pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor import (
    AlertManager,
    CheckResult,
    Config,
    PostFetchCheck,
    PushMonitorRegistry,
    Reporter,
    StatusLogger,
    WebsiteChecker,
    WebsiteMonitor,
    _deep_merge,
    _missing_security_headers,
    _nested_set,
    _parse_nslookup,
    render_prometheus_metrics,
)

# Make the project root importable regardless of cwd
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "reports").mkdir()
    return tmp_path


@pytest.fixture
def minimal_config(tmp_dir: Path) -> Config:
    cfg_file = tmp_dir / "config.json"
    cfg_file.write_text(json.dumps({
        "websites": ["https://example.com"],
        "timeout": 5,
        "max_response_time": 3000,
        "check_interval": 60,
        "max_retries": 1,
        "retry_delay": 1,
        "retry_backoff": 1.0,
        "concurrent_checks": 1,
    }))
    cfg = Config(cfg_file)
    # Override dirs to use tmp
    cfg.log_dir = tmp_dir / "logs"
    cfg.reports_dir = tmp_dir / "reports"
    return cfg


@pytest.fixture
def status_logger(tmp_dir: Path) -> StatusLogger:
    return StatusLogger(tmp_dir / "logs")


# =============================================================================
# CheckResult
# =============================================================================

class TestCheckResult:
    def test_is_healthy_up(self):
        r = CheckResult(url="x", status="UP", http_code=200,
                        response_time_ms=100, message="OK")
        assert r.is_healthy() is True

    def test_is_healthy_redirect(self):
        r = CheckResult(url="x", status="REDIRECT", http_code=301,
                        response_time_ms=50, message="")
        assert r.is_healthy() is True

    def test_is_not_healthy_down(self):
        r = CheckResult(url="x", status="DOWN", http_code=0,
                        response_time_ms=0, message="fail")
        assert r.is_healthy() is False

    def test_round_trip_dict(self):
        r = CheckResult(url="https://x.com", status="UP", http_code=200,
                        response_time_ms=120, message="OK",
                        ssl_days_remaining=90, keyword_found=True)
        assert CheckResult.from_dict(r.to_dict()) == r

    def test_timestamp_set_automatically(self):
        r = CheckResult(url="x", status="UP", http_code=200,
                        response_time_ms=1, message="")
        assert r.timestamp.endswith("Z")


# =============================================================================
# Helpers
# =============================================================================

class TestHelpers:
    def test_nested_set_simple(self):
        d: dict = {}
        _nested_set(d, "a.b.c", "val")
        assert d == {"a": {"b": {"c": "val"}}}

    def test_deep_merge_does_not_mutate_base(self):
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"d": 3}}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": {"c": 2, "d": 3}}
        assert base == {"a": 1, "b": {"c": 2}}

    def test_deep_merge_override_scalar(self):
        result = _deep_merge({"x": 1}, {"x": 99})
        assert result["x"] == 99


# =============================================================================
# Config
# =============================================================================

class TestConfig:
    def test_loads_from_file(self, minimal_config: Config):
        assert minimal_config.websites == ["https://example.com"]
        assert minimal_config.get("timeout") == 5

    def test_validation_rejects_bad_url(self, tmp_dir: Path):
        cfg_file = tmp_dir / "bad.json"
        cfg_file.write_text(json.dumps({
            "websites": ["ftp://nope.com"],
            "timeout": 5, "max_response_time": 1000,
            "check_interval": 60, "max_retries": 1,
            "retry_delay": 1, "retry_backoff": 1.0, "concurrent_checks": 1,
        }))
        with pytest.raises(ValueError, match="ftp"):
            Config(cfg_file)

    def test_validation_rejects_zero_timeout(self, tmp_dir: Path):
        cfg_file = tmp_dir / "bad.json"
        cfg_file.write_text(json.dumps({
            "websites": ["https://ok.com"],
            "timeout": 0, "max_response_time": 1000,
            "check_interval": 60, "max_retries": 1,
            "retry_delay": 1, "retry_backoff": 1.0, "concurrent_checks": 1,
        }))
        with pytest.raises(ValueError, match="timeout"):
            Config(cfg_file)

    def test_env_override_smtp_password(self, minimal_config: Config):
        with patch.dict(os.environ, {"MONITOR_SMTP_PASSWORD": "secret123"}):
            minimal_config._apply_env_overrides()
            assert minimal_config.get_nested(
                "alerts", "email", "smtp_password") == "secret123"

    def test_repr_redacts_password(self, minimal_config: Config):
        minimal_config._data["alerts"]["email"]["smtp_password"] = "hunter2"
        output = repr(minimal_config)
        assert "hunter2" not in output
        assert "***" in output


# =============================================================================
# WebsiteChecker
# =============================================================================

def _fake_response(status: int, body: bytes = b"hello", headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.read.return_value = body
    resp.headers = headers if headers is not None else {}
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestWebsiteChecker:
    @patch("monitor.urllib.request.urlopen")
    def test_up_200(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "UP"
        assert r.http_code == 200
        assert r.response_time_ms >= 0

    @patch("monitor.urllib.request.urlopen")
    def test_down_500(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(500)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "DOWN"

    @patch("monitor.urllib.request.urlopen")
    def test_redirect_301(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(301)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "REDIRECT"

    @patch("monitor.urllib.request.urlopen")
    def test_client_error_404(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(404)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "ERROR"

    @patch("monitor.urllib.request.urlopen")
    def test_slow_response(self, mock_open_url, minimal_config: Config):
        minimal_config._data["max_response_time"] = 1  # 1ms threshold
        mock_open_url.return_value = _fake_response(200)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        # Response will likely be > 1ms
        assert r.status in ("UP", "SLOW")  # timing dependent

    @patch("monitor.urllib.request.urlopen",
           side_effect=urllib.error.URLError("Connection refused"))
    def test_connection_refused(self, _, minimal_config: Config):
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://down.example.com")
        assert r.status == "DOWN"
        assert "Connection failed" in r.message

    @patch("monitor.urllib.request.urlopen")
    def test_keyword_found(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200, body=b"<html>Welcome</html>")
        minimal_config._data["keyword_checks"] = {"https://example.com": "Welcome"}
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.keyword_found is True

    @patch("monitor.urllib.request.urlopen")
    def test_keyword_not_found(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200, body=b"<html>Goodbye</html>")
        minimal_config._data["keyword_checks"] = {"https://example.com": "Welcome"}
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.keyword_found is False

    @patch("monitor.urllib.request.urlopen")
    def test_http_error(self, mock_open_url, minimal_config: Config):
        mock_open_url.side_effect = urllib.error.HTTPError(
            url="https://example.com", code=503,
            msg="Service Unavailable", hdrs=None, fp=None)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "DOWN"
        assert r.http_code == 503

    @patch("monitor.urllib.request.urlopen")
    def test_check_all_parallel(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200)
        minimal_config._data["websites"] = [
            "https://a.example.com",
            "https://b.example.com",
            "https://c.example.com",
        ]
        minimal_config._data["concurrent_checks"] = 3
        checker = WebsiteChecker(minimal_config)
        results = checker.check_all(minimal_config.websites)
        assert len(results) == 3
        # Order preserved
        assert results[0].url == "https://a.example.com"

    @patch("monitor.urllib.request.urlopen")
    def test_retry_logic(self, mock_open_url, minimal_config: Config):
        """Checker should retry on DOWN and return last result."""
        minimal_config._data["max_retries"] = 3
        minimal_config._data["retry_delay"] = 1
        mock_open_url.side_effect = urllib.error.URLError("Network error")
        checker = WebsiteChecker(minimal_config)
        r = checker.check_with_retry("https://flaky.example.com")
        assert r.status == "DOWN"
        assert mock_open_url.call_count == 3

    @patch("monitor.urllib.request.urlopen")
    def test_custom_method_and_body(self, mock_open_url, minimal_config: Config):
        """POST requests with a custom body should be sent as configured."""
        mock_open_url.return_value = _fake_response(200)
        minimal_config._data["request_overrides"] = {
            "https://example.com/api": {
                "method": "POST",
                "headers": {"X-Api-Key": "secret"},
                "body": '{"ping": true}',
            }
        }
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com/api")
        assert r.status == "UP"

        sent_request = mock_open_url.call_args[0][0]
        assert sent_request.get_method() == "POST"
        assert sent_request.get_header("X-api-key") == "secret"
        assert sent_request.data == b'{"ping": true}'

    @patch("monitor.urllib.request.urlopen")
    def test_default_method_is_get(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200)
        checker = WebsiteChecker(minimal_config)
        checker.check_once("https://example.com")
        sent_request = mock_open_url.call_args[0][0]
        assert sent_request.get_method() == "GET"
        assert sent_request.data is None

    @patch("monitor.urllib.request.urlopen")
    def test_security_headers_all_present(self, mock_open_url, minimal_config: Config):
        headers = {
            "Strict-Transport-Security": "max-age=31536000",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'",
            "Referrer-Policy": "no-referrer",
        }
        mock_open_url.return_value = _fake_response(200, headers=headers)
        minimal_config._data["security_headers_check"] = True
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.missing_security_headers == []

    @patch("monitor.urllib.request.urlopen")
    def test_security_headers_missing(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200, headers={})
        minimal_config._data["security_headers_check"] = True
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert "Strict-Transport-Security" in r.missing_security_headers
        assert "Content-Security-Policy" in r.missing_security_headers

    @patch("monitor.urllib.request.urlopen")
    def test_security_headers_check_disabled_by_default(self, mock_open_url, minimal_config: Config):
        mock_open_url.return_value = _fake_response(200, headers={})
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.missing_security_headers is None

    @patch("monitor.urllib.request.urlopen",
           side_effect=urllib.error.URLError("Connection refused"))
    def test_response_dependent_checks_skipped_on_down(self, _, minimal_config: Config):
        """keyword/security-header checks need a real response and must be
        skipped (not error) when the fetch itself fails."""
        minimal_config._data["keyword_checks"] = {"https://example.com": "hello"}
        minimal_config._data["security_headers_check"] = True
        checker = WebsiteChecker(minimal_config)
        r = checker.check_once("https://example.com")
        assert r.status == "DOWN"
        assert r.keyword_found is None
        assert r.missing_security_headers is None

    @patch("monitor.urllib.request.urlopen",
           side_effect=urllib.error.URLError("Connection refused"))
    def test_response_independent_checks_still_run_on_down(self, _, minimal_config: Config):
        """SSL/domain/DNS checks open their own connections and should still
        run even though the HTTP fetch failed."""
        minimal_config._data["dns_checks"] = {"example.com": ["A"]}
        checker = WebsiteChecker(minimal_config)
        with patch("monitor._ssl_days_remaining", return_value=99) as mock_ssl, \
             patch("monitor._resolve_dns", return_value={"A": ["1.2.3.4"]}) as mock_dns:
            r = checker.check_once("https://example.com")
        assert r.status == "DOWN"
        mock_ssl.assert_called_once()
        mock_dns.assert_called_once()
        assert r.dns_records == {"A": ["1.2.3.4"]}


# =============================================================================
# TCP port checks
# =============================================================================

class TestTcpChecks:
    @patch("monitor.socket.create_connection")
    def test_tcp_check_up(self, mock_connect, minimal_config: Config):
        mock_connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        checker = WebsiteChecker(minimal_config)
        r = checker.check_tcp_once("Database", "db.example.com", 5432)
        assert r.status == "UP"
        assert r.url == "tcp://db.example.com:5432"
        assert "TCP connect OK" in r.message
        mock_connect.assert_called_once_with(("db.example.com", 5432), timeout=5)

    @patch("monitor.socket.create_connection", side_effect=ConnectionRefusedError("refused"))
    def test_tcp_check_down_connection_refused(self, mock_connect, minimal_config: Config):
        checker = WebsiteChecker(minimal_config)
        r = checker.check_tcp_once("Database", "db.example.com", 5432)
        assert r.status == "DOWN"
        assert "connection failed" in r.message

    @patch("monitor.socket.create_connection", side_effect=socket.timeout("timed out"))
    def test_tcp_check_down_timeout(self, mock_connect, minimal_config: Config):
        checker = WebsiteChecker(minimal_config)
        r = checker.check_tcp_once("Database", "db.example.com", 5432)
        assert r.status == "DOWN"
        assert "timed out" in r.message

    @patch("monitor.socket.create_connection")
    def test_tcp_check_all_runs_each_once(self, mock_connect, minimal_config: Config):
        mock_connect.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_connect.return_value.__exit__ = MagicMock(return_value=False)
        checker = WebsiteChecker(minimal_config)
        results = checker.check_all_tcp({
            "DB": {"host": "db.example.com", "port": 5432},
            "Mail": {"host": "mail.example.com", "port": 25},
        })
        assert len(results) == 2
        assert mock_connect.call_count == 2
        assert {r.url for r in results} == {
            "tcp://db.example.com:5432", "tcp://mail.example.com:25",
        }

    def test_tcp_check_all_empty(self, minimal_config: Config):
        checker = WebsiteChecker(minimal_config)
        assert checker.check_all_tcp({}) == []


# =============================================================================
# Post-fetch check registry
# =============================================================================

class TestPostFetchCheckRegistry:
    def test_check_raising_does_not_crash_check_once(self, minimal_config: Config):
        """A buggy/failing check must not take down the whole check_once call."""
        def _boom(result, ctx):
            raise RuntimeError("simulated check failure")

        broken = PostFetchCheck("broken", _boom, requires_response=False)
        with patch("monitor.POST_FETCH_CHECKS", [broken]), \
             patch("monitor.urllib.request.urlopen") as mock_open_url:
            mock_open_url.return_value = _fake_response(200)
            checker = WebsiteChecker(minimal_config)
            r = checker.check_once("https://example.com")
        assert r.status == "UP"  # core fetch result unaffected by the broken check

    def test_requires_response_gate(self, minimal_config: Config):
        calls = []

        def _record(result, ctx):
            calls.append(ctx.url)

        gated = PostFetchCheck("gated", _record, requires_response=True)
        with patch("monitor.POST_FETCH_CHECKS", [gated]), \
             patch("monitor.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("down")):
            checker = WebsiteChecker(minimal_config)
            checker.check_once("https://example.com")
        assert calls == []  # never invoked because the fetch failed

    def test_default_registry_contains_expected_checks(self):
        from monitor import POST_FETCH_CHECKS
        names = {c.name for c in POST_FETCH_CHECKS}
        assert names == {
            "keyword", "security_headers", "ssl_expiry",
            "domain_expiry", "dns_records",
        }


# =============================================================================
# Security headers helper
# =============================================================================

class TestMissingSecurityHeaders:
    def test_all_missing(self):
        missing = _missing_security_headers({})
        assert "Strict-Transport-Security" in missing
        assert len(missing) == 5

    def test_case_insensitive_match(self):
        headers = {
            "strict-transport-security": "max-age=1",
            "x-content-type-options": "nosniff",
            "x-frame-options": "DENY",
            "content-security-policy": "default-src 'self'",
            "referrer-policy": "no-referrer",
        }
        assert _missing_security_headers(headers) == []


# =============================================================================
# DNS record parsing
# =============================================================================

class TestParseNslookup:
    def test_parse_a_record(self):
        output = (
            "Server:\t\t127.0.0.53\n"
            "Address:\t127.0.0.53#53\n\n"
            "Name:\texample.com\n"
            "Address: 93.184.216.34\n"
        )
        assert _parse_nslookup(output, "A") == ["93.184.216.34"]

    def test_parse_mx_record(self):
        output = "example.com\tmail exchanger = 10 mail.example.com.\n"
        assert _parse_nslookup(output, "MX") == ["10 mail.example.com."]

    def test_parse_txt_record(self):
        output = 'example.com\ttext = "v=spf1 include:_spf.example.com ~all"\n'
        assert _parse_nslookup(output, "TXT") == ["v=spf1 include:_spf.example.com ~all"]

    def test_parse_no_matches(self):
        assert _parse_nslookup("no useful output here", "A") == []


# =============================================================================
# Prometheus metrics rendering
# =============================================================================

class TestPrometheusMetrics:
    def test_renders_up_metric(self):
        results = [
            CheckResult(url="https://a.example.com", status="UP", http_code=200,
                       response_time_ms=120, message="OK"),
            CheckResult(url="https://b.example.com", status="DOWN", http_code=0,
                       response_time_ms=0, message="Connection failed"),
        ]
        output = render_prometheus_metrics(results)
        assert 'website_monitor_up{url="https://a.example.com"} 1' in output
        assert 'website_monitor_up{url="https://b.example.com"} 0' in output
        assert 'website_monitor_response_time_ms{url="https://a.example.com"} 120' in output
        assert "# TYPE website_monitor_up gauge" in output

    def test_renders_ssl_metric_only_when_present(self):
        results = [
            CheckResult(url="https://a.example.com", status="UP", http_code=200,
                       response_time_ms=10, message="OK", ssl_days_remaining=42),
        ]
        output = render_prometheus_metrics(results)
        assert 'website_monitor_ssl_days_remaining{url="https://a.example.com"} 42' in output

    def test_omits_ssl_section_when_absent(self):
        results = [
            CheckResult(url="https://a.example.com", status="UP", http_code=200,
                       response_time_ms=10, message="OK"),
        ]
        output = render_prometheus_metrics(results)
        assert "website_monitor_ssl_days_remaining" not in output


# =============================================================================
# StatusLogger
# =============================================================================

class TestStatusLogger:
    def _make_result(self, url: str, status: str = "UP",
                     hours_ago: float = 0) -> CheckResult:
        ts = datetime.utcnow() - timedelta(hours=hours_ago)
        return CheckResult(
            url=url, status=status, http_code=200 if status == "UP" else 0,
            response_time_ms=100, message="test",
            timestamp=ts.isoformat() + "Z",
        )

    def test_log_and_retrieve(self, status_logger: StatusLogger):
        r = self._make_result("https://x.com")
        status_logger.log(r)
        history = status_logger.history(hours=1)
        assert len(history) == 1
        assert history[0].url == "https://x.com"

    def test_history_time_filter(self, status_logger: StatusLogger):
        status_logger.log(self._make_result("https://recent.com", hours_ago=0.5))
        status_logger.log(self._make_result("https://old.com", hours_ago=25))
        history = status_logger.history(hours=24)
        urls = [r.url for r in history]
        assert "https://recent.com" in urls
        assert "https://old.com" not in urls

    def test_empty_history(self, status_logger: StatusLogger, tmp_dir: Path):
        logger = StatusLogger(tmp_dir / "logs" / "nonexistent")
        assert logger.history() == []

    def test_rotate(self, status_logger: StatusLogger):
        # Log one old, one recent
        status_logger.log(self._make_result("https://old.com", hours_ago=700))
        status_logger.log(self._make_result("https://new.com", hours_ago=1))
        status_logger.rotate(keep_days=10)
        history = status_logger.history(hours=9999)
        urls = [r.url for r in history]
        assert "https://new.com" in urls
        assert "https://old.com" not in urls


# =============================================================================
# Reporter
# =============================================================================

def _populate_history(logger: StatusLogger, url: str,
                      statuses: list[str], hours_back: float = 1) -> None:
    for i, status in enumerate(statuses):
        ts = datetime.utcnow() - timedelta(hours=hours_back) + timedelta(minutes=i)
        logger.log(CheckResult(
            url=url, status=status,
            http_code=200 if status == "UP" else 500,
            response_time_ms=200, message="",
            timestamp=ts.isoformat() + "Z",
        ))


class TestReporter:
    def _populate(self, logger: StatusLogger, url: str,
                  statuses: list[str], hours_back: float = 1) -> None:
        _populate_history(logger, url, statuses, hours_back)

    def test_report_contains_url(self, status_logger: StatusLogger):
        self._populate(status_logger, "https://test.com", ["UP"] * 10)
        reporter = Reporter(status_logger)
        report = reporter.generate(hours=24)
        assert "https://test.com" in report

    def test_report_uptime_100(self, status_logger: StatusLogger):
        self._populate(status_logger, "https://perfect.com", ["UP"] * 5)
        reporter = Reporter(status_logger)
        report = reporter.generate(hours=24)
        assert "100.00%" in report

    def test_report_partial_uptime(self, status_logger: StatusLogger):
        self._populate(status_logger, "https://flaky.com",
                       ["UP"] * 3 + ["DOWN"] * 1)
        reporter = Reporter(status_logger)
        report = reporter.generate(hours=24)
        assert "75.00%" in report

    def test_report_no_data(self, status_logger: StatusLogger):
        reporter = Reporter(status_logger)
        report = reporter.generate(hours=1)
        assert "No monitoring data" in report

    def test_report_ssl_warning(self, status_logger: StatusLogger):
        ts = datetime.utcnow().isoformat() + "Z"
        status_logger.log(CheckResult(
            url="https://expiring.com", status="UP", http_code=200,
            response_time_ms=100, message="",
            timestamp=ts, ssl_days_remaining=5,
        ))
        reporter = Reporter(status_logger)
        report = reporter.generate(hours=24)
        assert "EXPIRES SOON" in report or "5 days" in report

    def test_latest_status_by_timestamp_not_log_order(self, status_logger: StatusLogger):
        """Regression: 'latest' must be the most recent timestamp, not the
        last-appended log line — logs are not always written in order
        (e.g. after a rotate(), or a replayed/merged log file)."""
        now = datetime.utcnow()
        # Deliberately log the MORE recent timestamp FIRST.
        status_logger.log(CheckResult(
            url="https://x.com", status="DOWN", http_code=500,
            response_time_ms=0, message="recent failure",
            timestamp=now.isoformat() + "Z", ssl_days_remaining=10,
        ))
        status_logger.log(CheckResult(
            url="https://x.com", status="UP", http_code=200,
            response_time_ms=100, message="older success",
            timestamp=(now - timedelta(minutes=30)).isoformat() + "Z",
            ssl_days_remaining=99,
        ))
        reporter = Reporter(status_logger)
        stats = reporter.compute_stats(hours=24)
        assert stats["https://x.com"]["latest_status"] == "DOWN"
        assert stats["https://x.com"]["ssl_days_remaining"] == 10

    def test_compute_stats_structure(self, status_logger: StatusLogger):
        self._populate(status_logger, "https://struct.com",
                       ["UP", "UP", "DOWN", "SLOW"])
        reporter = Reporter(status_logger)
        stats = reporter.compute_stats(hours=24)
        s = stats["https://struct.com"]
        assert s["total_checks"] == 4
        assert s["counts"]["UP"] == 2
        assert s["counts"]["DOWN"] == 1
        assert s["counts"]["SLOW"] == 1
        assert s["uptime_pct"] == 75.0  # UP+SLOW count as healthy
        assert s["avg_response_ms"] == 200.0

    def test_compute_stats_empty(self, status_logger: StatusLogger):
        reporter = Reporter(status_logger)
        assert reporter.compute_stats(hours=24) == {}


# =============================================================================
# DashboardServer API (tested via handler logic, not a live socket)
# =============================================================================

class TestDashboardServerData:
    """Exercise the data-producing side (Reporter/StatusLogger) the way the
    dashboard's HTTP handler consumes it, without spinning up a real socket."""

    def test_api_status_healthy_when_all_up(self, status_logger: StatusLogger):
        _populate_history(status_logger, "https://a.com", ["UP", "UP"])
        _populate_history(status_logger, "https://b.com", ["UP"])
        reporter = Reporter(status_logger)
        stats = reporter.compute_stats(hours=24)
        healthy = all(s["latest_status"] in ("UP", "SLOW", "REDIRECT") for s in stats.values())
        assert healthy is True

    def test_api_status_unhealthy_when_any_down(self, status_logger: StatusLogger):
        _populate_history(status_logger, "https://a.com", ["UP"])
        _populate_history(status_logger, "https://b.com", ["DOWN"])
        reporter = Reporter(status_logger)
        stats = reporter.compute_stats(hours=24)
        healthy = all(s["latest_status"] in ("UP", "SLOW", "REDIRECT") for s in stats.values())
        assert healthy is False

    def test_history_json_serializable(self, status_logger: StatusLogger):
        _populate_history(status_logger, "https://a.com", ["UP", "DOWN"])
        results = status_logger.history(hours=24)
        # This is exactly what DashboardServer's /api/history does.
        payload = json.dumps([r.to_dict() for r in results])
        assert "https://a.com" in payload


# =============================================================================
# Push / heartbeat monitoring
# =============================================================================

class TestPushMonitorRegistry:
    def _registry(self, tmp_path: Path, timeout_seconds: int = 3600) -> PushMonitorRegistry:
        monitors = {"backup-job": {"name": "Nightly Backup", "timeout_seconds": timeout_seconds}}
        return PushMonitorRegistry(monitors, tmp_path / "push_state.json")

    def test_unknown_push_id_rejected(self, tmp_path: Path):
        registry = self._registry(tmp_path)
        assert registry.record_heartbeat("does-not-exist") is False

    def test_no_heartbeat_yet_is_unknown(self, tmp_path: Path):
        registry = self._registry(tmp_path)
        results = registry.check_all()
        assert len(results) == 1
        assert results[0].status == "UNKNOWN"
        assert results[0].url == "push://backup-job"

    def test_heartbeat_marks_up(self, tmp_path: Path):
        registry = self._registry(tmp_path)
        assert registry.record_heartbeat("backup-job", "all good") is True
        results = registry.check_all()
        assert results[0].status == "UP"
        assert "all good" in results[0].message

    def test_expired_heartbeat_marks_down(self, tmp_path: Path):
        registry = self._registry(tmp_path, timeout_seconds=0)
        registry.record_heartbeat("backup-job")
        time.sleep(0.05)
        results = registry.check_all()
        assert results[0].status == "DOWN"
        assert "no heartbeat" in results[0].message

    def test_state_persists_across_instances(self, tmp_path: Path):
        """A new registry instance backed by the same state file must pick
        up a heartbeat recorded by a previous instance (e.g. after a
        process restart)."""
        state_path = tmp_path / "push_state.json"
        monitors = {"backup-job": {"name": "Nightly Backup", "timeout_seconds": 3600}}

        first = PushMonitorRegistry(monitors, state_path)
        first.record_heartbeat("backup-job", "first instance")

        second = PushMonitorRegistry(monitors, state_path)
        results = second.check_all()
        assert results[0].status == "UP"
        assert "first instance" in results[0].message

    def test_corrupt_state_file_starts_fresh(self, tmp_path: Path):
        state_path = tmp_path / "push_state.json"
        state_path.write_text("{not valid json")
        registry = PushMonitorRegistry(
            {"backup-job": {"name": "Nightly Backup", "timeout_seconds": 3600}}, state_path
        )
        results = registry.check_all()
        assert results[0].status == "UNKNOWN"


# =============================================================================
# Maintenance windows
# =============================================================================

class TestMaintenanceWindows:
    def _monitor_with_windows(self, minimal_config: Config, windows: list) -> WebsiteMonitor:
        minimal_config._data["maintenance_windows"] = windows
        return WebsiteMonitor(minimal_config)

    def test_no_windows_never_in_maintenance(self, minimal_config: Config):
        monitor = self._monitor_with_windows(minimal_config, [])
        assert monitor._in_maintenance("https://example.com") is False

    def test_active_window_scoped_to_url(self, minimal_config: Config):
        now = datetime.utcnow()
        windows = [{
            "start": (now - timedelta(minutes=5)).isoformat() + "Z",
            "end": (now + timedelta(minutes=5)).isoformat() + "Z",
            "urls": ["https://a.example.com"],
        }]
        monitor = self._monitor_with_windows(minimal_config, windows)
        assert monitor._in_maintenance("https://a.example.com") is True
        assert monitor._in_maintenance("https://other.example.com") is False

    def test_expired_window_scoped_to_url_does_not_apply(self, minimal_config: Config):
        now = datetime.utcnow()
        windows = [{
            "start": (now - timedelta(hours=2)).isoformat() + "Z",
            "end": (now - timedelta(hours=1)).isoformat() + "Z",
            "urls": ["https://a.example.com"],
        }]
        monitor = self._monitor_with_windows(minimal_config, windows)
        assert monitor._in_maintenance("https://a.example.com") is False

    def test_future_window_does_not_apply_yet(self, minimal_config: Config):
        now = datetime.utcnow()
        windows = [{
            "start": (now + timedelta(hours=1)).isoformat() + "Z",
            "end": (now + timedelta(hours=2)).isoformat() + "Z",
            "urls": ["https://a.example.com"],
        }]
        monitor = self._monitor_with_windows(minimal_config, windows)
        assert monitor._in_maintenance("https://a.example.com") is False

    def test_blanket_window_applies_to_all_urls(self, minimal_config: Config):
        now = datetime.utcnow()
        windows = [{
            "start": (now - timedelta(minutes=5)).isoformat() + "Z",
            "end": (now + timedelta(minutes=5)).isoformat() + "Z",
            "urls": [],
        }]
        monitor = self._monitor_with_windows(minimal_config, windows)
        assert monitor._in_maintenance("https://anything.example.com") is True

    def test_malformed_window_is_skipped_not_fatal(self, minimal_config: Config):
        windows = [{"start": "not-a-date", "end": "also-not-a-date", "urls": []}]
        monitor = self._monitor_with_windows(minimal_config, windows)
        assert monitor._in_maintenance("https://example.com") is False

    def test_alerts_suppressed_during_maintenance(self, minimal_config: Config):
        now = datetime.utcnow()
        minimal_config._data["maintenance_windows"] = [{
            "start": (now - timedelta(minutes=5)).isoformat() + "Z",
            "end": (now + timedelta(minutes=5)).isoformat() + "Z",
            "urls": ["https://example.com"],
        }]
        monitor = WebsiteMonitor(minimal_config)
        with patch.object(monitor.alerts, "send_alerts") as mock_send, \
             patch.object(monitor.checker, "check_all",
                         return_value=[CheckResult(url="https://example.com", status="DOWN",
                                                    http_code=500, response_time_ms=0,
                                                    message="down")]):
            monitor.check_all(quiet=True)
        mock_send.assert_not_called()


# =============================================================================
# ntfy / Pushover alert channels
# =============================================================================

class TestNtfyAndPushoverAlerts:
    def _alert_manager(self, tmp_path: Path, **alert_overrides) -> AlertManager:
        cfg = Config.__new__(Config)
        cfg.script_dir = tmp_path
        cfg.log_dir = tmp_path / "logs"
        cfg.reports_dir = tmp_path / "reports"
        cfg.log_dir.mkdir(exist_ok=True)
        cfg.reports_dir.mkdir(exist_ok=True)
        base_alerts = {
            "email": {"enabled": False}, "webhook": {"enabled": False},
            "slack": {"enabled": False}, "ntfy": {"enabled": False},
            "pushover": {"enabled": False}, "desktop": {"enabled": False},
        }
        base_alerts.update(alert_overrides)
        cfg._data = {
            "ssl_expiry_warn_days": 14, "domain_expiry_warn_days": 30,
            "alerts": base_alerts,
        }
        return AlertManager(cfg)

    def _result(self, status="DOWN") -> CheckResult:
        return CheckResult(url="https://example.com", status=status, http_code=500,
                           response_time_ms=0, message="Simulated failure")

    @patch("monitor.urllib.request.urlopen")
    def test_ntfy_sends_when_enabled(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, ntfy={
            "enabled": True, "server": "https://ntfy.sh", "topic": "my-topic",
        })
        am._ntfy(self._result())
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://ntfy.sh/my-topic"
        assert req.get_header("Priority") == "urgent"
        assert b"Simulated failure" in req.data

    @patch("monitor.urllib.request.urlopen")
    def test_ntfy_skipped_when_disabled(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, ntfy={"enabled": False, "topic": "my-topic"})
        am._ntfy(self._result())
        mock_open.assert_not_called()

    @patch("monitor.urllib.request.urlopen")
    def test_ntfy_skipped_when_no_topic(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, ntfy={"enabled": True, "topic": ""})
        am._ntfy(self._result())
        mock_open.assert_not_called()

    @patch("monitor.urllib.request.urlopen", side_effect=urllib.error.URLError("down"))
    def test_ntfy_failure_does_not_raise(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, ntfy={
            "enabled": True, "server": "https://ntfy.sh", "topic": "my-topic",
        })
        am._ntfy(self._result())  # must not raise

    @patch("monitor.urllib.request.urlopen")
    def test_pushover_sends_when_enabled(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, pushover={
            "enabled": True, "user_key": "uKey", "api_token": "aToken",
        })
        am._pushover(self._result())
        req = mock_open.call_args[0][0]
        assert req.full_url == "https://api.pushover.net/1/messages.json"
        body = req.data.decode()
        assert "token=aToken" in body
        assert "user=uKey" in body
        assert "priority=1" in body  # DOWN -> high priority

    @patch("monitor.urllib.request.urlopen")
    def test_pushover_low_priority_when_not_down(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, pushover={
            "enabled": True, "user_key": "uKey", "api_token": "aToken",
        })
        am._pushover(self._result(status="ERROR"))
        req = mock_open.call_args[0][0]
        assert "priority=0" in req.data.decode()

    @patch("monitor.urllib.request.urlopen")
    def test_pushover_skipped_when_missing_credentials(self, mock_open, tmp_path: Path):
        am = self._alert_manager(tmp_path, pushover={
            "enabled": True, "user_key": "", "api_token": "aToken",
        })
        am._pushover(self._result())
        mock_open.assert_not_called()

    def test_config_repr_redacts_ntfy_and_pushover_secrets(self, tmp_path: Path):
        cfg = Config.__new__(Config)
        cfg.script_dir = tmp_path
        cfg.log_dir = tmp_path / "logs"
        cfg.reports_dir = tmp_path / "reports"
        cfg.log_dir.mkdir(exist_ok=True)
        cfg.reports_dir.mkdir(exist_ok=True)
        cfg._data = {
            "alerts": {
                "email": {"smtp_password": ""},
                "slack": {"webhook_url": ""}, "webhook": {"url": ""},
                "ntfy": {"topic": "secret-topic-name"},
                "pushover": {"user_key": "secret-user-key", "api_token": "secret-api-token"},
            }
        }
        output = repr(cfg)
        assert "secret-topic-name" not in output
        assert "secret-user-key" not in output
        assert "secret-api-token" not in output
        assert "***" in output
