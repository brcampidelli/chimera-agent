---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
---

# Chimera auf einem Server (VPS) deployen

Chimera läuft als lang laufender **Gateway**-Prozess. Mit `--cron` löst er zusätzlich geplante
Jobs nach einer echten Uhr aus, er *handelt also zeitgesteuert* (nicht nur, wenn ihm eine
Nachricht geschickt wird). Diese Anleitung deckt ein Deployment auf einem 5-$-VPS auf zwei
Wegen ab: **Docker Compose** (empfohlen) oder **systemd**.

Zustand — Langzeitgedächtnis, Cron-Jobs, Trajectories, das Audit-Log — lebt in `CHIMERA_HOME`
(ein Verzeichnis). Wird es persistiert (ein Docker-Volume oder ein echter Pfad), übersteht der
Agent Neustarts.

---

## 0. Voraussetzungen

- Ein Linux-VPS (1 vCPU / 1 GB RAM reicht für einen einzelnen Agenten locker).
- Mindestens ein Provider-Key. Der günstigste Einstieg ist ein OpenRouter-Key.
- Für öffentlich erreichbare eingehende Webhooks (WhatsApp Cloud API, `POST /webhook/<hook>`)
  eine Domain + ein Reverse-Proxy mit TLS (Caddy oder nginx). Für Discord/Telegram/Slack/Signal
  nicht nötig, die verbinden sich ausgehend.

Die Env-Datei aus der Vorlage erstellen und einen Key eintragen:

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (empfohlen)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

Das führt `chimera serve --host 0.0.0.0 --cron` aus: das HTTP-Gateway (`/chat`,
`/webhook/<hook>`, `/health`) **plus** den Cron-Daemon. Der Zustand wird im Volume
`chimera-data` persistiert.

**Eine Chat-Plattform bedienen** (hier Discord) — den Token in `.env` setzen, dann den Befehl
in `docker-compose.yml` überschreiben:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

und erneut `docker compose up -d`. (Telegram/Slack/Signal funktionieren über ihre jeweiligen
Flags genauso; jede braucht ihren passenden `CHIMERA_*`-Token — siehe `.env.example`.)

**Auf eine neue Version aktualisieren:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (ohne Docker)

In eine Virtualenv auf dem Host installieren:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

`/etc/systemd/system/chimera.service` anlegen:

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

## 3. Proaktive Arbeit planen (der `--cron`-Daemon)

`--cron` *führt* nur die Jobs aus, die geplant wurden. Sie werden über die CLI hinzugefügt (sie
werden in `CHIMERA_HOME` persistiert):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Innerhalb von Docker:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

Der Daemon tickt alle `--cron-tick` Sekunden (Standard 30) und leitet die Aktion jedes fälligen
Jobs an den Agenten weiter. Ein fehlschlagender Job wird protokolliert und stoppt den Daemon
nie.

---

## 4. Health, Backups, Sicherheit

- **Health:** `GET /health` liefert `{"ok": true}`. Compose hat einen Healthcheck verdrahtet.
- **Backups:** das Volume `chimera-data` (Docker) oder das Verzeichnis `CHIMERA_HOME`
  (systemd) sichern — das ist der gesamte dauerhafte Zustand. Beispiel:
  `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Secrets:** Keys in `.env` halten (git-ignoriert); nie ins Image einbacken.
- **Exposition:** das Gateway nur hinter einer Firewall/einem Reverse-Proxy an `0.0.0.0`
  binden. **`CHIMERA_SERVER_TOKEN`** setzen, um `Authorization: Bearer <token>` für das
  HTTP-Gateway und die Desktop-API zu verlangen (der Desktop-UI wird der Token automatisch nur
  für Loopback-Clients übergeben, sodass eine remote exponierte Instanz hinter der eigenen
  Auth bleibt). Auth ist opt-in und standardmäßig leer — ohne diese Variable gibt es also
  keine: den Port einschränken oder nur den Webhook-Pfad exponieren.
- **Sandboxing:** `CHIMERA_SANDBOX=docker` setzen, um die Shell-/Code-Tools in einem
  Wegwerf-Container statt auf dem Host laufen zu lassen.
- **Unbeaufsichtigte Host-Ausführung:** seit dem 20.07.2026 **verweigert** ein Headless-Lauf
  Host-Befehle unter dem Standard `CHIMERA_HOST_EXEC=ask` (es gibt kein TTY zum Bestätigen).
  Ein Deployment, das den Agenten wirklich Shell-Befehle auf dem Host ausführen lassen muss,
  setzt bewusst `CHIMERA_HOST_EXEC=allow`; die sicherere Option ist `CHIMERA_SANDBOX=docker`,
  wo das Gate übersprungen wird, weil der Container wirklich isoliert. Ebenso aktiviert der
  API-Server die Taint-Einengung (`CHIMERA_TAINT_NARROW=1`): Nachdem der Agent nicht
  vertrauenswürdigen Inhalt gelesen hat, schlagen Ausführungs-/Schreib-/Outbound-Tools
  sicherheitshalber fehl. Auf `0` setzen, um weiter autonom zu handeln.

---

## 5. Ehrlicher Status

Chimera ist **Alpha**. Das hier deployt und läuft, und der Cron-Daemon macht es proaktiv — aber
es hat noch **keine Produktionslaufleistung**. Mit risikoarmen Crons anfangen, `logs`
beobachten und die Governance-Leitplanken (`--guard` bei `solve`, `CHIMERA_SANDBOX=docker`) für
alles im Hinterkopf behalten, was echte Systeme berührt.

## Wo diese Seiten veröffentlicht werden

Diese Dateien sind die Quelle für die Dokumentation auf **chimeraagent.space**, die sie direkt
aus diesem Verzeichnis zur Build-Zeit rendert. Das Markdown hier bearbeiten, und die Seite folgt;
es gibt keine zweite Kopie, die synchron gehalten werden muss.

Die MkDocs-Konfiguration, die früher unter `mkdocs.yml` lag, wurde entfernt. Sie war
vollständig — Theme, Navigation, zehn Seiten — und wurde nie veröffentlicht: Es gab keinen
Workflow und keinen `gh-pages`-Branch, sodass die Deploy-Anleitung, die früher an dieser Stelle
stand, eine Seite beschrieb, die nicht existierte. Eine Konfiguration, die niemand ausführt, ist
schlimmer als keine Konfiguration, denn die nächste Person bearbeitet ihre Navigation und kann
nicht herausfinden, warum sich nichts ändert.
