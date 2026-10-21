# syntax=docker/dockerfile:1.7
# ---- 1. build the Svelte PWA to static files -----------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN mkdir -p ../src/tandem/server/static && npm run build

# ---- 2. Python runtime ------------------------------------------------------------------------------
FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra postgres --extra mcp --no-install-project
COPY src/ ./src/
COPY examples/ ./examples/
COPY --from=ui /src/src/tandem/server/static/ ./src/tandem/server/static/
RUN uv sync --frozen --no-dev --extra postgres --extra mcp \
 && useradd --system --uid 10001 --home /app tandem \
 && mkdir -p /data && chown tandem /data
USER 10001
# With no provider key and no tandem.yaml, Tandem runs in mock mode with the demo data.
ENV TANDEM_HOST=0.0.0.0 TANDEM_PORT=8000 TANDEM_DATA_DIR=/data
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
CMD ["tandem", "serve", "--forwarded-allow-ips", "*"]
