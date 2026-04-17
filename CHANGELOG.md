# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [0.0.2] — Unreleased

### Breaking changes
- `report` now requires an explicit `--hours` flag for non-default windows
  (`python monitor.py report --hours 168`)
- `check` exits with code **1** when any site is unhealthy (useful in CI/CD)
- Log directory is no longer created at import time; only at `Config()` init

### Added
- **Concurrent checks** — all sites checked in parallel via `ThreadPoolExecutor`
- **Keyword monitoring** — verify page content contains expected text
- **SSL expiry tracking** — days-remaining reported per site; ⚠️  warning < 14 days
- **Exponential back-off** — configurable `retry_backoff` multiplier
- **`rotate` command** — prune old JSONL log entries in-place
- **`--out FILE`** flag on `report` — write report to file
- **Environment variable secrets** — `MONITOR_SMTP_PASSWORD`, `MONITOR_SLACK_WEBHOOK`,
  `MONITOR_WEBHOOK_URL`, `MONITOR_SMTP_USER`
- **SIGTERM handler** — graceful shutdown in continuous mode
- **Rotating log handler** — `RotatingFileHandler` (10 MB × 7 backups)
- **Docker / docker-compose** support with resource limits and healthcheck
- **GitHub Actions CI** — tests on 3 Python versions × 3 OSes + Docker build
- **Full test suite** — pytest covering config, checker, logger, reporter

### Fixed
- Scripts were embedded in a bash generator instead of being standalone files
- Missing `__init__.py` in tests (now unnecessary with pytest auto-discovery)
- No exit codes — CLI now returns meaningful codes for scripting
- Desktop notification on Windows fell through silently — now has PS fallback
- No config validation — invalid URLs and zero-values now raise `ValueError`
- Secrets were stored plain-text in JSON — now read from environment variables
- No log rotation — large deployments no longer fill disk

### Changed
- `Config.get_nested(*keys)` replaces chained `.get()` calls
- `CheckResult` is a `dataclass` (serializable, comparable, type-safe)
- All installer scripts are standalone files in `scripts/` (not a bash generator)

---

## [0.0.1] — Initial release

- Basic HTTP/HTTPS monitoring with urllib
- Single-threaded sequential checks
- Email, webhook, Slack, desktop alerts
- JSON Lines status log
- 24h uptime report
- Cross-platform installer scripts (Windows, macOS, Linux)
