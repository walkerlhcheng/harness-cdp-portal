#!/bin/bash
set -e

# Start tailscaled in background if TS_AUTHKEY is set
if [ -n "$TS_AUTHKEY" ]; then
  echo "[harness] Starting tailscaled..."
  tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055 &
  sleep 2
  tailscale up --authkey="$TS_AUTHKEY" --hostname="harness-railway" --accept-routes
  echo "[harness] Tailscale connected"
else
  echo "[harness] No TS_AUTHKEY set - Tailscale disabled"
fi

echo "[harness] Starting FastAPI..."
exec uv run uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
