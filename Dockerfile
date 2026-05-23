FROM python:3.11-slim

    # Install system dependencies + Tailscale (official install script)
    RUN apt-get update && apt-get install -y curl iptables iproute2 ca-certificates gnupg && \
        curl -fsSL https://tailscale.com/install.sh | sh && \
        rm -rf /var/lib/apt/lists/*

    # Install uv
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

    WORKDIR /app

    # Copy dependency files and install
    COPY pyproject.toml .
    RUN uv sync --no-dev

    # Copy app files
    COPY . .

    # Startup script
    RUN chmod +x /app/start.sh

    EXPOSE 8000

    CMD ["/app/start.sh"]
    