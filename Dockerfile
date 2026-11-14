# Chimera Agent — container image for running the gateway (+ cron daemon) on a server.
# Build:  docker build -t chimera-agent .
# Run:    docker run -d --env-file .env -p 8765:8765 -v chimera-data:/data chimera-agent
# (or use docker-compose.yml). State (memory, crons, trajectories, audit) lives in /data.

# Pinned by digest: a tag is a pointer its owner moves, and `python:3.12-slim` moves every few weeks.
# Refresh both digests together, deliberately (`docker buildx imagetools inspect <image>`), so the
# image only changes when someone chose it. Dependabot's `docker` ecosystem can do this on a schedule.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# System packages:
#   git    — git-worktree isolation (solve --isolate, IsolatedCrew, solve-batch)
#   curl   — some skills shell out to curl for HTTP
#   ffmpeg — audio/video decoding for speech-to-text (transcribe_audio) and media download (yt-dlp)
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv: the project's package manager (fast, deterministic installs).
# (digest of uv 0.13.8)
COPY --from=ghcr.io/astral-sh/uv@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /uvx /bin/

WORKDIR /app
COPY . /app

# Install Chimera batteries-included: the `[full]` extra bundles the messaging adapters
# (Discord/Slack), MCP, documents (docx/pdf/xlsx→md, and back: `documents-out` for the
# create_document tool), media download (yt-dlp), speech-to-text
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
# The browsers go to a fixed, world-readable path: the default is the installing user's home, which
# is root's, and the non-root user below would not find them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python3 -m playwright install --with-deps chromium && chmod -R a+rX /ms-playwright

# Persist agent state outside the image; mount a volume here.
ENV CHIMERA_HOME=/data

# Not root. The agent runs shell commands and fetches the web on a model's say-so; as root, any escape
# from the in-process guards is an escape with the whole container. /data is created AND chowned
# before VOLUME, so a fresh named volume inherits the owner. A volume that already holds root-owned
# state from an older image needs `chown -R 10001:10001` once.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin chimera     && mkdir -p /data && chown -R chimera:chimera /data /app
VOLUME /data
USER chimera

EXPOSE 8765

# Default: the HTTP gateway bound to all interfaces + the cron daemon (proactivity on).
# Override for a chat platform, e.g.:  docker run ... chimera-agent serve --host 0.0.0.0 --cron --discord
# `sh <file>` rather than an executable bit: the repository normalizes modes and line endings, and a
# script that only works with +x breaks the build on a checkout that lost it.
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["serve", "--host", "0.0.0.0", "--cron"]
