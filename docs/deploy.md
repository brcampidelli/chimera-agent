# Deploying Chimera on a server (VPS)

Chimera runs as a long-lived **gateway** process. Add `--cron` and it also fires scheduled
jobs on a real clock, so it *acts on time* (not just when messaged). This guide covers a
$5 VPS deployment two ways: **Docker Compose** (recommended) or **systemd**.

State — long-term memory, cron jobs, trajectories, the audit log — lives in `CHIMERA_HOME`
(a directory). Persist it (a Docker volume or a real path) and the agent survives restarts.

---

## 0. Prerequisites

- A Linux VPS (1 vCPU / 1 GB RAM is plenty for a single agent).
- At least one provider key. The cheapest start is an OpenRouter key.
- For public inbound webhooks (WhatsApp Cloud API, `POST /webhook/<hook>`), a domain +
  a reverse proxy with TLS (Caddy or nginx). Not needed for Discord/Telegram/Slack/Signal,
  which connect outbound.

Create your env file from the template and fill in a key:

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (recommended)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

That runs `chimera serve --host 0.0.0.0 --cron`: the HTTP gateway (`/chat`, `/webhook/<hook>`,
`/health`) **plus** the cron daemon. State persists in the `chimera-data` volume.

**Serve a chat platform** (Discord shown) — set the token in `.env`, then override the command
in `docker-compose.yml`:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

and `docker compose up -d` again. (Telegram/Slack/Signal work the same via their flags; each
needs its matching `CHIMERA_*` token — see `.env.example`.)

**Update to a new version:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (no Docker)

Install into a virtualenv on the host:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Create `/etc/systemd/system/chimera.service`:

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. Scheduling proactive work (the `--cron` daemon)

`--cron` only *runs* the jobs you've scheduled. Add them with the CLI (they persist in
`CHIMERA_HOME`):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Inside Docker:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

The daemon ticks every `--cron-tick` seconds (default 30) and dispatches each job's action
through the agent when it's due. A failing job is logged and never stops the daemon.

---

## 4. Health, backups, security

- **Health:** `GET /health` returns `{"ok": true}`. Compose has a healthcheck wired.
- **Backups:** back up the `chimera-data` volume (Docker) or `CHIMERA_HOME` dir (systemd) —
  that's all the durable state. Example: `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Secrets:** keep keys in `.env` (git-ignored); never bake them into the image.
- **Exposure:** bind the gateway to `0.0.0.0` only behind a firewall/reverse proxy. Set
  **`CHIMERA_SERVER_TOKEN`** to require `Authorization: Bearer <token>` on the HTTP gateway and the
  desktop API (the desktop UI is handed the token automatically only for loopback clients, so a
  remotely-exposed instance stays behind your own auth). Auth is opt-in and empty by default, so
  without that variable there is none — restrict the port, or expose only the webhook path.
- **Sandboxing:** set `CHIMERA_SANDBOX=docker` to run the shell/code tools in a throwaway
  container instead of the host.
- **Unattended host execution:** since 2026-07-20 a headless run **refuses** host commands under the
  default `CHIMERA_HOST_EXEC=ask` (there is no TTY to confirm on). A deployment that genuinely needs
  the agent to run shell on the host sets `CHIMERA_HOST_EXEC=allow` deliberately; the safer option is
  `CHIMERA_SANDBOX=docker`, where the gate is skipped because the container really isolates. Likewise
  the API server arms taint narrowing (`CHIMERA_TAINT_NARROW=1`): after the agent reads untrusted
  content, execution/write/outbound tools fail closed. Set it to `0` to keep acting autonomously.

---

## 5. Honest status

Chimera is **alpha**. This deploys and runs, and the cron daemon makes it proactive — but it
has **no production mileage** yet. Start with low-stakes crons, watch `logs`, and keep the
governance guardrails (`--guard` on `solve`, `CHIMERA_SANDBOX=docker`) in mind for anything
that touches real systems.

## Publish the docs site (GitHub Pages)

The site builds with `uv run --extra docs mkdocs build --strict`. To auto-deploy it on every
docs change, enable GitHub Pages (Settings → Pages → Source: GitHub Actions) and add this
workflow at `.github/workflows/docs.yml` (committing a workflow file needs a token with the
`workflow` scope):

```yaml
name: docs
on:
  push:
    branches: [main]
    paths: ["docs/**", "mkdocs.yml", ".github/workflows/docs.yml"]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv run --extra docs mkdocs build --strict
      - uses: actions/upload-pages-artifact@v3
        with: { path: site }
  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment: { name: github-pages, url: "${{ steps.deployment.outputs.page_url }}" }
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```
