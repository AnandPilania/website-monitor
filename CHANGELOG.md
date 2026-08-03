# Changelog

All notable changes to this project will be documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [0.0.5] — Unreleased

### Added
- **Push / heartbeat monitoring** — `push_monitors` config registers passive
  monitors that expect an external process (cron job, backup script, batch
  worker) to call back in periodically. `GET`/`POST /api/push/<id>` on the
  dashboard server records a heartbeat; if none arrives within
  `timeout_seconds`, the monitor flips to DOWN. State persists to
  `logs/push_state.json` so it survives restarts. This is the one class of
  failure active (outbound) checks structurally can't catch — anything with
  no listening port to poll.
- **TCP port checks** — `tcp_checks` config opens a raw socket connection to
  `host:port` (databases, mail servers, game servers, custom protocols),
  reusing the exact same retry/concurrency/logging/alerting pipeline as HTTP
  checks via a synthetic `tcp://host:port` pseudo-URL.
- **Maintenance windows** — `maintenance_windows` config suppresses alerts
  (but keeps checking and logging) during planned downtime. Each window has
  a `start`/`end` timestamp and an optional `urls` list; omit or empty
  `urls` to apply the window to every configured check.
- **ntfy.sh and Pushover alert channels** — both are plain HTTP POST
  integrations requiring no SDK. `alerts.ntfy` / `alerts.pushover` follow
  the same enable/credentials pattern as the existing Slack/webhook
  channels, with `MONITOR_NTFY_TOPIC`, `MONITOR_PUSHOVER_USER_KEY`, and
  `MONITOR_PUSHOVER_API_TOKEN` env var overrides.
- 26 new tests covering TCP checks, push monitor lifecycle (including a
  restart-persistence test and a corrupt-state-file recovery test),
  maintenance window edge cases (expired/future/blanket/malformed windows),
  and ntfy/Pushover request construction (84 total, all passing)

### Fixed
- `Config.__repr__` was only redacting the SMTP password — Slack webhook
  URLs and the generic webhook URL were printed in plain text by
  `python monitor.py config` (and anywhere the config gets logged) if those
  channels were configured. Now redacted alongside SMTP password, and the
  new ntfy/Pushover credentials are redacted from the same code path.

### Changed
- `check` output now also displays TCP and push-monitor results alongside
  HTTP checks

---

## [0.0.4] — Unreleased

### Added
- **Web dashboard + REST API** — `python monitor.py dashboard` (or
  `dashboard.enabled: true` in config to run alongside `monitor`) serves a
  self-contained, dependency-free live status page plus JSON endpoints:
  `GET /api/status`, `GET /api/history?hours=N`, `GET /api/uptime?hours=N`.
  No framework, no build step, no CDN — the page is stdlib `http.server`
  serving inline HTML/CSS/JS that polls the JSON API every 15 seconds.
- `Reporter.compute_stats()` — the per-URL aggregation previously buried
  inside `Reporter.generate()`'s text formatting is now a reusable method
  returning plain, JSON-serializable dicts. Both the text report and the
  dashboard API consume the same computation, so they can't drift apart.
- 6 new tests covering `compute_stats()`, the dashboard's data layer, and a
  regression test for the ordering bug below (58 total, all passing)

### Fixed
- **Latest-check selection used log-append order instead of timestamp
  order.** `Reporter` picked `checks[-1]` (and `ssl_values[-1]` /
  `domain_values[-1]`) assuming the log file's last line was always the
  most recent check. This happened to hold for normal sequential runs, but
  broke silently after `rotate()`, or if a log file was ever merged/replayed
  out of order — the dashboard/report would show a stale status as "latest."
  Now explicitly selects by `max(checks, key=lambda c: c.timestamp)`.

---

## [0.0.3] — Unreleased

### Added
- **Custom requests** — `request_overrides` config lets any URL be checked
  with a custom HTTP method, headers, and body (enables POST/PUT health
  checks and GraphQL endpoint monitoring)
- **Security headers audit** — `security_headers_check` flags missing
  `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`,
  `Content-Security-Policy`, and `Referrer-Policy`
- **Domain expiry monitoring** — `domain_expiry_check` looks up registration
  expiry via public RDAP (no `python-whois` dependency)
- **DNS record checks** — `dns_checks` resolves A/AAAA/CNAME/MX/TXT/NS per
  hostname via the system `nslookup` (falls back to the stdlib resolver for
  A/AAAA when `nslookup` isn't available)
- **Prometheus metrics export** — `python monitor.py metrics` for one-shot
  text-exposition output, or `prometheus.enabled` to run a background
  `/metrics` HTTP server during `monitor`
- **Pluggable post-fetch check registry** — `POST_FETCH_CHECKS` is now an
  ordered list of small, independent `PostFetchCheck` objects (keyword,
  security headers, SSL expiry, domain expiry, DNS) instead of inline
  conditionals in `check_once()`. Adding a new check means writing one
  function and appending it to the list — see the comment block above
  `POST_FETCH_CHECKS` in `monitor.py`. A failing check is caught and logged
  without crashing the rest of the checks or the overall result.
- 19 new tests covering all of the above (52 total, all passing)

### Changed
- `AlertManager` now also fires when SSL/domain expiry drops below a warn
  threshold (`ssl_expiry_warn_days`, `domain_expiry_warn_days`) or security
  headers are missing — previously only fired on DOWN/ERROR status
- `CheckResult` gained `domain_days_remaining`, `missing_security_headers`,
  and `dns_records` fields (all optional, `None`/omitted when not checked)
- Terminal display, email, and Slack alerts updated to surface the new fields
- Checks that need a live HTTP response (keyword, security headers) are now
  automatically skipped — not silently wrong — when the fetch itself fails;
  checks that open their own connection (SSL, domain expiry, DNS) still run
  regardless of HTTP outcome, exactly as before the refactor

### Fixed
- README's `--config` usage example had the flag in the wrong position
  (global flags must precede the subcommand: `monitor.py --config X check`,
  not `monitor.py check --config X`)

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
