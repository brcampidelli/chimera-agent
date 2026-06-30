# Docker — execution sandbox

Chimera can run its **shell tool** inside an isolated Docker container instead of
directly on the host. This is the recommended setting whenever the agent runs
untrusted or self-written commands (Tier-2 `solve`, the Kanban `solve` lane, …).

## What it isolates

Each command runs as:

```
docker run --rm --network none --memory 512m \
    -v <workspace>:/workspace -w /workspace <image> sh -c "<command>"
```

- **`--rm`** — the container filesystem is discarded after every command.
- **`--network none`** — no network from inside the sandbox by default.
- **`--memory 512m`** — capped memory.
- **`-v <workspace>:/workspace`** — only the workspace is shared (read-write), so file
  edits persist; nothing else on the host is visible.

If Docker isn't available, the sandbox **falls back to local execution** so the agent
keeps working (without container isolation) rather than failing.

## Use it

```bash
# Build the sandbox image (once)
docker build -t chimera-sandbox -f docker/Dockerfile.sandbox .

# Point Chimera at it
export CHIMERA_SANDBOX=docker
export CHIMERA_SANDBOX_IMAGE=chimera-sandbox   # or any image, e.g. python:3.12-slim

uv run chimera solve "add a /health route and a test" --workspace ./scratch \
  --verify "pytest -q"
```

The default (`CHIMERA_SANDBOX=local`) runs on the host, exactly as before.
