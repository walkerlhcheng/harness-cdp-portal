FROM python:3.11-slim

# Install tailscale
RUN apt-get update && apt-get install -y curl iptables iproute2 && \
    curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.nodesource.gpg | gpg --dearmor -o /usr/share/keyrings/tailscale-archive-keyring.gpg && \
    curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.tailscale-keyring.list | sed 's|signed-by=.*|signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg|' > /etc/apt/sources.list.d/tailscale.list && \
    apt-get update && apt-get install -y tailscale && \
    rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency files
COPY pyproject.toml .

# Install dependencies with uv
RUN uv sync --no-dev

COPY . .

# Startup script
COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 8000

CMD ["/start.sh"]
