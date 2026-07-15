# syntax=docker/dockerfile:1.7
# ---------- Stage 1: dependencies -----------------------------------------
FROM python:3.12-slim AS deps

# Disable bytecode writing and force unbuffered output (cleaner container logs).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install OS-level deps needed for building wheels (cryptography uses OpenSSL).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: runtime ----------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# Run as a non-root user; the token never needs root privileges.
RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --no-create-home --shell /sbin/nologin app

WORKDIR /app

# Copy the installed packages from the deps stage.
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY --from=deps /usr/bin/tini /usr/bin/tini

# Copy only the runtime assets needed by the service and migrations.
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./alembic.ini
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
 && chown -R app:app /app

USER app

# Defaults can be overridden at runtime.
ENV GITHUB_ORGANIZATION="" \
    API_HOST="0.0.0.0" \
    API_PORT="8000" \
    DATABASE_URL="sqlite+aiosqlite:///./github_api.db"

EXPOSE 8000

# The .env file MUST be mounted at runtime. We do not bake secrets into the
# image. Common usage:
#   docker run --rm -p 8000:8000 \
#       -v "$PWD/.env:/app/.env:ro" \
#       gh-api-service:latest
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["serve"]
