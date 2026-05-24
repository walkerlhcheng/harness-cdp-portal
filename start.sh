#!/bin/bash
set -e

# ─── Tailscale (only if TS_AUTHKEY is set) ───────────────────────────────────
if [ -n "$TS_AUTHKEY" ]; then
  echo "[harness] Starting tailscaled (userspace mode)..."
  tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 &
  TSPID=$!

  # Wait for tailscaled socket to appear
  for i in $(seq 1 15); do
    if tailscale status --peers=false >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  tailscale up \
    --authkey="$TS_AUTHKEY" \
    --hostname="harness-railway" \
    --accept-routes \
    --accept-dns=false \
    --timeout=30s || echo "[harness] WARNING: tailscale up failed — continuing without VPN"

  TS_IP=$(tailscale ip -4 2>/dev/null || echo "unknown")
  echo "[harness] Tailscale up — this container IP: ${TS_IP}"
  echo "[harness] ⚠️  NOTE: This Tailscale IP changes on every Railway redeploy!"
  echo "[harness]    Update CDP_HOST in Railway env vars if local machine IP changed."
  export RAILWAY_TAILSCALE_IP="${TS_IP}"

  # ─── socat bridge: local 19222 → SOCKS5 proxy → remote CDP host ──────────
  # This allows the Python app to connect to 127.0.0.1:19222 while socat
  # tunnels the traffic through the Tailscale SOCKS5 proxy to the real target.
  REMOTE_CDP_HOST="${CDP_HOST:-100.113.104.72}"
  REMOTE_CDP_PORT="${CDP_PORT:-19222}"

  echo "[harness] Starting socat bridge: 127.0.0.1:${REMOTE_CDP_PORT} → SOCKS5 → ${REMOTE_CDP_HOST}:${REMOTE_CDP_PORT}"
  socat TCP4-LISTEN:${REMOTE_CDP_PORT},fork,reuseaddr \
    SOCKS5:127.0.0.1:${REMOTE_CDP_HOST}:${REMOTE_CDP_PORT},socksport=1055 &
  SOCAT_PID=$!
  echo "[harness] socat bridge PID: ${SOCAT_PID}"

  # Override CDP_HOST so the app connects through the bridge instead of direct IP
  export CDP_HOST=127.0.0.1
  echo "[harness] CDP_HOST overridden to 127.0.0.1 (socat bridge active)"

else
  echo "[harness] No TS_AUTHKEY set — Tailscale skipped (CDP must be reachable directly)"
fi

# ─── Start app ────────────────────────────────────────────────────────────────
echo "[harness] Starting uvicorn on port ${PORT:-8000}..."
exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
