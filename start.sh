#!/bin/bash
    set -e

    # Start tailscaled in background if TS_AUTHKEY is set
    if [ -n "$TS_AUTHKEY" ]; then
      echo "[harness] Starting tailscaled..."
      tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 &
      sleep 3
      tailscale up --authkey="$TS_AUTHKEY" --hostname="harness-railway" --accept-routes --accept-dns=false
      echo "[harness] Tailscale up"
    else
      echo "[harness] No TS_AUTHKEY - Tailscale skipped"
    fi

    echo "[harness] Starting uvicorn on port ${PORT:-8000}..."
    exec uv run uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
    