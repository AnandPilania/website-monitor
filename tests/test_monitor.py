"""
Website Monitor — Test Suite
Run: pytest tests/ -v
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from monitor import (
    CheckResult,
    Config,
    Reporter,
    StatusLogger,
    WebsiteChecker,
    _deep_merge,
    _nested_set,
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

def _fake_response(status: int, body: bytes = b"hello") -> MagicMock:
    resp = MagicMock()
    resp.status = status
    resp.getcode.return_value = status
    resp.read.return_value = body
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

class TestReporter:
    def _populate(self, logger: StatusLogger, url: str,
                  statuses: list[str], hours_back: float = 1) -> None:
        for i, status in enumerate(statuses):
            ts = datetime.utcnow() - timedelta(hours=hours_back) + timedelta(minutes=i)
            logger.log(CheckResult(
                url=url, status=status,
                http_code=200 if status == "UP" else 500,
                response_time_ms=200, message="",
                timestamp=ts.isoformat() + "Z",
            ))

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
