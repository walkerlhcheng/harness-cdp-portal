#!/bin/bash
set -e

# ─── Tailscale (only if TS_AUTHKEY is set) ───────────────────────────────────
if [ -n "$TS_AUTHKEY" ]; then
  echo "[harness] Starting tailscaled (userspace mode)..."
  # --socks5-server exposes the Tailscale network as a SOCKS5 proxy on port 1055
  # Python app (main.py) uses this proxy directly for all CDP HTTP + WebSocket calls
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
  echo "[harness] SOCKS5 proxy active on localhost:1055 — Python app will route CDP traffic through it"
  export RAILWAY_TAILSCALE_IP="${TS_IP}"

else
  echo "[harness] No TS_AUTHKEY set — Tailscale skipped (CDP must be reachable directly)"
fi

# ─── Start app ────────────────────────────────────────────────────────────────
echo "[harness] Starting uvicorn on port ${PORT:-8000}..."
exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
