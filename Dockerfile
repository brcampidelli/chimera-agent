# Chimera Agent — container image for running the gateway (+ cron daemon) on a server.
# Build:  docker build -t chimera-agent .
# Run:    docker run -d --env-file .env -p 8765:8765 -v chimera-data:/data chimera-agent
# (or use docker-compose.yml). State (memory, crons, trajectories, audit) lives in /data.

FROM python:3.12-slim

# System packages:
#   git    — git-worktree isolation (solve --isolate, IsolatedCrew, solve-batch)
#   curl   — some skills shell out to curl for HTTP
#   ffmpeg — audio/video decoding for speech-to-text (transcribe_audio) and media download (yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv: the project's package manager (fast, deterministic installs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
COPY . /app

# Install Chimera batteries-included: the `[full]` extra bundles the messaging adapters
# (Discord/Slack), MCP, documents (docx/pdf/xlsx→md), media download (yt-dlp), speech-to-text
# (faster-whisper), data analysis (pandas/scikit-learn) and charts (matplotlib/seaborn/plotly),
# plus YouTube transcripts — so every non-GPU feature works out of the box in this image. The
# GPU-heavy extras (`imagegen-local`, `train`) stay opt-in. For a minimal image, use '.[messaging,mcp]'.
RUN uv pip install --system --no-cache '.[full]'

# Bake the browser into the image so the `browser` tool works out of the box in the container.
# Playwright is a core dependency, but pip only installs the Python package — the Chromium binary
# AND its system libraries come from Playwright's own CLI. `--with-deps` installs both; the libs are
# what a slim image lacks (without them Chromium downloads but won't launch). Doing this at build time
# (instead of the tool's first-use auto-download) also avoids a ~150MB fetch on the first request.
# Adds ~400MB to the image — the cost of a real browser. To skip it, comment this line out.
RUN python3 -m playwright install --with-deps chromium

# Persist agent state outside the image; mount a volume here.
ENV CHIMERA_HOME=/data
RUN mkdir -p /data
VOLUME /data

EXPOSE 8765

# Default: the HTTP gateway bound to all interfaces + the cron daemon (proactivity on).
# Override for a chat platform, e.g.:  docker run ... chimera-agent serve --host 0.0.0.0 --cron --discord
ENTRYPOINT ["chimera"]
CMD ["serve", "--host", "0.0.0.0", "--cron"]
