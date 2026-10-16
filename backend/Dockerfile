# Build context: repository root (the seed needs examples/).
#   docker build -f backend/Dockerfile --target api .
#   docker build -f backend/Dockerfile --target worker .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

FROM base AS deps
COPY backend/requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# --------------------------------------------------------------------------- api
# Internet-facing service: non-root, no Docker access.
FROM base AS api
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
COPY --from=deps /install /usr/local
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY examples ./examples
RUN mkdir -p /data && chown app:app /data
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
# --proxy-headers: the API is only reachable through Traefik on a private network.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "20", \
     "--no-server-header"]

# --------------------------------------------------------------------------- worker / agent
# Builds images and manages containers through the Docker socket. Access to the socket is
# root-equivalent, so this image is isolated from user code and never exposed publicly.
FROM api AS worker
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:28-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
HEALTHCHECK NONE
CMD ["celery", "-A", "app.workers.celery_app", "worker", "--loglevel", "INFO", \
     "-Q", "deployments,ops,maintenance", "--concurrency", "2", "--prefetch-multiplier", "1"]
