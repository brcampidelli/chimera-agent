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
  To reach this instance from the desktop app, see [§5](#5-reaching-this-instance-from-the-desktop-app).
- **Sandboxing:** set `CHIMERA_SANDBOX=docker` to run the shell/code tools in a throwaway
  container instead of the host.
- **Unattended host execution:** since 2026-07-20 a headless run **refuses** host commands under the
  default `CHIMERA_HOST_EXEC=ask` (there is no TTY to confirm on). A deployment that genuinely needs
  the agent to run shell on the host sets `CHIMERA_HOST_EXEC=allow` deliberately; the safer option is
  `CHIMERA_SANDBOX=docker`, where the gate is skipped because the container really isolates. Likewise
  the API server arms taint narrowing (`CHIMERA_TAINT_NARROW=1`): after the agent reads untrusted
  content, execution/write/outbound tools fail closed. Set it to `0` to keep acting autonomously.

---

## 5. Reaching this instance from the desktop app

The desktop app talks to the Chimera it starts on your own machine by default. From v0.44 it can
also point at one you run yourself — this VPS — so the app becomes a window onto the agent that is
already up all night doing your cron jobs.

**Read this part before opening a port.** What you are exposing is not a dashboard. Every screen in
that app is a command surface: it runs shell, edits files, dispatches a board of autonomous tasks
and changes settings. An instance reachable from the internet without a token is not "a Chimera
someone could look at" — it is a machine anybody who finds the address can run commands on, with
your provider keys paying for it.

Three things have to be true, and the app refuses to connect unless the first two are:

**1 — TLS.** Put it behind a reverse proxy with a real certificate (Caddy gets one for you):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

The app refuses a non-`https` address outside your own machine, because the token travels in an
`Authorization` header on **every** request — over plain http that is a credential handed to every
hop between you and the server, and nothing on screen would look wrong while it happened.

**2 — A token.** Auth is opt-in and empty by default:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Put it in the `.env`, restart, and paste the same value into the app. The app refuses a remote
address with no token for the reason above: an instance without one is open to whoever finds it.

Note what the server deliberately does **not** do: when a remote client asks for the UI, it serves
the page *without* the token. The token is never handed out over the network — you copy it to your
own client, once, out of band. That is why the app has a field for it.

**3 — Your app's origin.** The app is served by its own local sidecar, so its requests to this
instance are cross-origin and a browser discards the responses unless this instance names that
origin:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

The app shows you the exact value when a connection fails — it is on the error message, ready to
copy. The port is stable per install (it is remembered between launches since v0.43), so you set
this once per machine you connect from. Several are comma-separated.

**This setting is not a security boundary and must not be read as one.** CORS decides which *page*
may read a response; it decides nothing about who may *call*. The token is the gate. Naming an
origin without setting a token does not protect anything — it just makes an unprotected instance
reachable from a browser as well as from `curl`.

Empty by default, so an instance nobody configured behaves exactly as it did before.

### What the app tells you when it fails

- **"The token was refused"** — the address and the origin are right; the value is wrong.
- **"Could not reach it"** — either the address is wrong or the origin is not allowed. The browser
  refuses to say which, on purpose, so the app names both rather than guessing and hands you the
  origin to allow.
- **A version warning** — the app compares its own backend's version against this one and says both
  numbers. It does not refuse: a server one release behind usually works, and refusing would strand
  you on the screen you would need to fix it. Some endpoints may not exist on the older side.

### Safer still

Skip the public port entirely: reach the VPS over WireGuard or a Tailscale tailnet and point the
app at the private address. The token still matters — a tailnet is a smaller room, not an empty one.

---

## 6. Honest status

Chimera is **alpha**. This deploys and runs, and the cron daemon makes it proactive — but it
has **no production mileage** yet. Start with low-stakes crons, watch `logs`, and keep the
governance guardrails (`--guard` on `solve`, `CHIMERA_SANDBOX=docker`) in mind for anything
that touches real systems.

## Where these pages are published

These files are the source for the documentation on **chimeraagent.space**, which renders them
straight from this directory at build time. Edit the markdown here and the site follows; there is
no second copy to keep in step.

The MkDocs configuration that used to live at `mkdocs.yml` has been removed. It was complete —
theme, navigation, ten pages — and it was never published: there was no workflow and no
`gh-pages` branch, so the deploy instructions that used to sit in this spot described a site that
did not exist. A configuration nobody runs is worse than no configuration, because the next person
edits its navigation and cannot work out why nothing changes.
