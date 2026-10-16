#!/bin/sh
# Starts Traefik with its static configuration as CLI flags. The token that authenticates the
# dynamic-config poll is read from the generated secrets volume (or INTERNAL_API_TOKEN).
set -eu

TOKEN="${INTERNAL_API_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f /run/paas-secrets/internal_api_token ]; then
  TOKEN="$(cat /run/paas-secrets/internal_api_token)"
fi
if [ -z "$TOKEN" ]; then
  echo "internal API token missing (secrets volume not initialised?)" >&2
  exit 1
fi

exec traefik \
  --global.checkNewVersion=false \
  --global.sendAnonymousUsage=false \
  --entryPoints.web.address=:80 \
  --entryPoints.web.transport.respondingTimeouts.readTimeout=600s \
  --providers.file.filename=/etc/traefik/dynamic.yml \
  --providers.file.watch=true \
  --providers.http.endpoint="http://backend:8000/internal/traefik/config?token=${TOKEN}" \
  --providers.http.pollInterval=2s \
  --providers.http.pollTimeout=2s \
  --ping=true \
  --log.level="${TRAEFIK_LOG_LEVEL:-INFO}" \
  --log.format=json \
  --accessLog=true \
  --accessLog.format=json \
  --accessLog.fields.headers.defaultMode=drop
