FROM python:3.11-slim

    # Install uv
    COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

    WORKDIR /app

    # Copy dependency files and install
    COPY pyproject.toml uv.lock* ./
    RUN uv sync --no-dev --frozen || uv sync --no-dev

    # Copy app files
    COPY . .

    # Tailscale install (optional - only runs if needed at build time)
    RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && \
        curl -fsSL https://tailscale.com/install.sh | sh && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

    RUN chmod +x /app/start.sh

    EXPOSE 8000
    CMD ["/app/start.sh"]
    