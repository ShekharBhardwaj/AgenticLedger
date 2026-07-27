# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install source resolution:
#   - The release pipeline drops a freshly built wheel into dist/ before
#     `docker build`; when present, it wins.
#   - A cold build from a plain checkout has only dist/.gitkeep, so the build
#     falls back to the published release on PyPI. Pin a version with
#     `--build-arg AGENTICLEDGER_VERSION=0.4.0`.
ARG AGENTICLEDGER_VERSION=
COPY dist/ /tmp/dist/
RUN set -eu; \
    WHL=$(find /tmp/dist -name '*.whl' | head -n 1); \
    if [ -n "$WHL" ]; then \
        pip install --no-cache-dir "${WHL}[otel]"; \
    else \
        pip install --no-cache-dir "agentic-ledger[otel]${AGENTICLEDGER_VERSION:+==${AGENTICLEDGER_VERSION}}"; \
    fi; \
    rm -rf /tmp/dist

# Drop root: the proxy runs as a dedicated system user. /data (the SQLite
# volume) is owned by that user; named volumes inherit this ownership on first
# mount. Bind mounts on Linux hosts need `chown 10001` on the host directory —
# see docs/deployment.md.
RUN useradd --uid 10001 --create-home --home-dir /home/agenticledger --shell /usr/sbin/nologin agenticledger \
    && mkdir -p /data \
    && chown agenticledger:agenticledger /data
USER agenticledger

ENV AGENTICLEDGER_HOST=0.0.0.0
ENV AGENTICLEDGER_PORT=8000
ENV AGENTICLEDGER_DSN=sqlite:////data/agenticledger.db
ENV AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('AGENTICLEDGER_PORT', '8000') + '/health', timeout=4)"]

CMD ["python", "-m", "agenticledger.proxy"]
