# Chimera Agent — container image for running the gateway (+ cron daemon) on a server.
# Build:  docker build -t chimera-agent .
# Run:    docker run -d --env-file .env -p 8765:8765 -v chimera-data:/data chimera-agent
# (or use docker-compose.yml). State (memory, crons, trajectories, audit) lives in /data.

FROM python:3.12-slim

# git: required for the git-worktree isolation (solve --isolate, IsolatedCrew, solve-batch).
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# uv: the project's package manager (fast, deterministic installs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . /app

# Install Chimera + the native messaging adapters (Discord/Slack) and the MCP client.
# Telegram/Signal are plain HTTP and need no extra; WhatsApp is a webhook.
RUN uv pip install --system --no-cache '.[messaging,mcp]'

# Persist agent state outside the image; mount a volume here.
ENV CHIMERA_HOME=/data
RUN mkdir -p /data
VOLUME /data

EXPOSE 8765

# Default: the HTTP gateway bound to all interfaces + the cron daemon (proactivity on).
# Override for a chat platform, e.g.:  docker run ... chimera-agent serve --host 0.0.0.0 --cron --discord
ENTRYPOINT ["chimera"]
CMD ["serve", "--host", "0.0.0.0", "--cron"]
