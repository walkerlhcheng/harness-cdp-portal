# Stage 1: grab Tailscale binaries from official image
    FROM tailscale/tailscale:stable AS tailscale-bin

    # Stage 2: main app
    FROM python:3.11-slim

    # Install uv
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

    # Copy Tailscale binaries from official image (no apt needed)
    COPY --from=tailscale-bin /usr/local/bin/tailscale /usr/local/bin/tailscale
    COPY --from=tailscale-bin /usr/local/bin/tailscaled /usr/local/bin/tailscaled

    WORKDIR /app

    # Copy dependency files and install
    COPY pyproject.toml uv.lock* ./
    RUN uv sync --no-dev --frozen || uv sync --no-dev

    # Copy app files
    COPY . .

    RUN chmod +x /app/start.sh

    EXPOSE 8000
    CMD ["/app/start.sh"]
    
