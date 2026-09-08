---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
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

### Fragen, was der Zeitplan verschweigt

```bash
chimera cron doctor
```

Ein Zeitplan kann auf zwei Arten verstummen, und bis jemand nachfragt, sehen beide gleich aus — wie
ein Zeitplan, bei dem nichts fällig ist:

- **Nichts lief.** Der Daemon ist gestorben, der Container wurde nie neu gestartet, der Host hat
  geschlafen. Keine Exception, keine Log-Zeile, kein Urteil. Jeder andere Ehrlichkeitsmechanismus
  hier sitzt *stromabwärts eines tatsächlich stattgefundenen Laufs*, also kommt keiner von ihnen an
  die Reihe.
- **Alles lief und alles scheiterte.** Der Daemon lebt, `last_run` liegt eine Minute zurück, der
  Zeitplan schreitet voran — und seit einem Monat schlägt jede Weiterleitung fehl. Dieser Fall
  liest sich *gesünder* als der erste, weil das Feld, das nach Gesundheit aussieht, den Versuch
  aufzeichnet und nicht das Ergebnis.

`cron doctor` stellt beide Fragen und gibt unterschiedliche Ratschläge, weil die Behebungen nichts
gemeinsam haben: überfällig betrifft den Daemon, fehlschlagend betrifft den Job. `chimera cron list`
gibt eine Zeile aus, sobald irgendetwas fehlschlägt, sodass man den Befehl nicht kennen muss, um
davon zu erfahren.

**Was es nicht ist.** Es ist eine Frage, kein Wächter: Solange der Prozess unten ist, bemerkt hier
nichts etwas, aus demselben Grund, aus dem ein abgestürzter Prozess seinen eigenen Absturz nicht
protokollieren kann. Es antwortet ehrlich in dem Moment, in dem irgendetwas fragt — eine Shell, die
App, der nächste Start. Ein echter Watchdog braucht seine eigene Uhr und seine eigene Lebendigkeit,
was eine eigene Entscheidung ist und
[als Issue #26 verfolgt wird](https://github.com/brcampidelli/chimera-agent/issues/26). Wer eine
Warnung statt einer Antwort will, führt es aus dem Cron des Hosts selbst aus:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

Das funktioniert, weil es von etwas anderem als Chimera beaufsichtigt wird — worin genau der Sinn
liegt.

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
  keine: den Port einschränken oder nur den Webhook-Pfad exponieren. Wie diese Instanz aus der
  Desktop-App erreicht wird, steht in [§5](#5-reaching-this-instance-from-the-desktop-app).
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

## 5. Diese Instanz aus der Desktop-App erreichen

Die Desktop-App spricht standardmäßig mit der Chimera, die sie auf der eigenen Maschine startet. Ab
v0.44 kann sie auch auf eine selbst betriebene zeigen — diesen VPS — und wird damit zum Fenster auf
den Agenten, der ohnehin die ganze Nacht die eigenen Cron-Jobs erledigt.

**Diesen Teil lesen, bevor ein Port geöffnet wird.** Was hier exponiert wird, ist kein Dashboard.
Jeder Bildschirm dieser App ist eine Befehlsoberfläche: Sie führt Shell aus, bearbeitet Dateien,
verteilt ein Board autonomer Aufgaben und ändert Einstellungen. Eine aus dem Internet erreichbare
Instanz ohne Token ist nicht „eine Chimera, die sich jemand ansehen könnte" — sie ist eine Maschine,
auf der jeder, der die Adresse findet, Befehle ausführen kann, bezahlt mit den eigenen
Provider-Keys.

Drei Dinge müssen zutreffen, und ohne die ersten beiden verweigert die App die Verbindung:

**1 — TLS.** Hinter einen Reverse-Proxy mit echtem Zertifikat stellen (Caddy besorgt eines):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

Die App verweigert eine Adresse ohne `https` außerhalb der eigenen Maschine, weil der Token bei
**jeder** Anfrage in einem `Authorization`-Header mitreist — über einfaches http ist das ein
Zugangsdatum, das jedem Hop zwischen der eigenen Maschine und dem Server ausgehändigt wird, und auf
dem Bildschirm sähe währenddessen nichts falsch aus.

**2 — Ein Token.** Auth ist opt-in und standardmäßig leer:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Ihn in die `.env` eintragen, neu starten und denselben Wert in die App einfügen. Die App verweigert
aus dem obigen Grund eine entfernte Adresse ohne Token: Eine Instanz ohne Token steht jedem offen,
der sie findet.

Zu beachten, was der Server bewusst **nicht** tut: Fragt ein entfernter Client die UI an, liefert er
die Seite *ohne* den Token aus. Der Token wird nie über das Netz herausgegeben — er wird einmal,
außerhalb des Kanals, in den eigenen Client kopiert. Deshalb hat die App ein Feld dafür.

**3 — Der Origin der eigenen App.** Die App wird von ihrem eigenen lokalen Sidecar ausgeliefert,
also sind ihre Anfragen an diese Instanz cross-origin, und ein Browser verwirft die Antworten,
solange diese Instanz jenen Origin nicht nennt:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

Die App zeigt den genauen Wert an, wenn eine Verbindung fehlschlägt — er steht in der Fehlermeldung,
bereit zum Kopieren. Der Port ist pro Installation stabil (er wird seit v0.43 zwischen Starts
gemerkt), das wird also einmal pro Maschine gesetzt, von der aus verbunden wird. Mehrere werden
durch Kommas getrennt.

**Diese Einstellung ist keine Sicherheitsgrenze und darf nicht als solche gelesen werden.** CORS
entscheidet, welche *Seite* eine Antwort lesen darf; über den, der *aufrufen* darf, entscheidet es
nichts. Das Gate ist der Token. Einen Origin zu nennen, ohne einen Token zu setzen, schützt nichts —
es macht eine ungeschützte Instanz nur zusätzlich zu `curl` auch aus einem Browser erreichbar.

Standardmäßig leer, sodass sich eine Instanz, die niemand konfiguriert hat, genau wie zuvor verhält.

### Was die App bei einem Fehlschlag meldet

- **„Der Token wurde abgelehnt"** — Adresse und Origin stimmen; der Wert ist falsch.
- **„Nicht erreichbar"** — entweder ist die Adresse falsch oder der Origin ist nicht erlaubt. Der
  Browser sagt absichtlich nicht, welches von beidem, also nennt die App beides, statt zu raten, und
  liefert den Origin gleich mit, der erlaubt werden muss.
- **Eine Versionswarnung** — die App vergleicht die Version ihres eigenen Backends mit dieser und
  nennt beide Zahlen. Sie verweigert nicht: Ein Server, der ein Release zurückliegt, funktioniert
  meist, und eine Verweigerung würde ausgerechnet den Bildschirm sperren, den man zum Beheben
  bräuchte. Manche Endpunkte gibt es auf der älteren Seite womöglich nicht.

### Noch sicherer

Den öffentlichen Port ganz auslassen: den VPS über WireGuard oder ein Tailscale-Tailnet erreichen
und die App auf die private Adresse zeigen lassen. Der Token bleibt trotzdem wichtig — ein Tailnet
ist ein kleinerer Raum, kein leerer.

---

## 6. Ehrlicher Status

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
