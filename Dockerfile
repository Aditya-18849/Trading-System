# ── Stage 1: Builder ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install system dependencies for psycopg2 and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL maintainer="Algo Trading System"
LABEL description="SEBI-Compliant Retail Algo Trading System"

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone to IST for SEBI compliance timestamps
ENV TZ=Asia/Kolkata
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Install Playwright browsers (for fallback token refresh)
RUN playwright install chromium --with-deps 2>/dev/null || true

# Copy application code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY tests/ ./tests/

# Copy Alembic configuration
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# Copy env example for reference
COPY .env.example ./.env.example

# Create non-root user for security
RUN groupadd -r trader && useradd -r -g trader -s /bin/false trader

# Create log directory writable by the non-root user
RUN mkdir -p /app/logs && chown -R trader:trader /app/logs

USER trader

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with Uvicorn — 2 workers for a small trading system
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]
