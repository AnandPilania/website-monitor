# =============================================================================
# Website Monitor — Dockerfile
# =============================================================================
# Build:  docker build -t website-monitor .
# Run:    docker run -d --name monitor -v $(pwd)/data:/app/data website-monitor
# =============================================================================

# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ── Runtime user (non-root) ───────────────────────────────────────────────────
RUN groupadd -r monitor && useradd -r -g monitor -s /sbin/nologin monitor

# ── Copy source ───────────────────────────────────────────────────────────────
COPY monitor.py             ./
COPY monitor_config.json    ./

# ── Create writable directories owned by the runtime user ─────────────────────
RUN mkdir -p logs reports \
    && chown -R monitor:monitor /app

# ── Drop privileges ───────────────────────────────────────────────────────────
USER monitor

# ── Healthcheck ───────────────────────────────────────────────────────────────
# Runs a check; exit code 0 = all sites healthy, 1 = at least one down
HEALTHCHECK --interval=5m --timeout=60s --start-period=10s --retries=2 \
    CMD python monitor.py check --config /app/monitor_config.json || exit 1

# ── Default command: continuous monitoring ────────────────────────────────────
CMD ["python", "monitor.py", "monitor"]
