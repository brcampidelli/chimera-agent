---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
---

# Distribuire Chimera su un server (VPS)

Chimera gira come processo **gateway** di lunga durata. Aggiungi `--cron` e attiva anche job
pianificati su un orologio reale, così *agisce nel tempo* (non solo quando gli si scrive). Questa
guida copre una distribuzione su VPS da $5 in due modi: **Docker Compose** (consigliato) o
**systemd**.

Lo stato — memoria a lungo termine, job cron, traiettorie, il log di audit — vive in
`CHIMERA_HOME` (una directory). Persistilo (un volume Docker o un percorso reale) e l'agente
sopravvive ai riavvii.

---

## 0. Prerequisiti

- Un VPS Linux (1 vCPU / 1 GB di RAM basta per un singolo agente).
- Almeno una chiave provider. Il modo più economico per iniziare è una chiave OpenRouter.
- Per webhook pubblici in entrata (WhatsApp Cloud API, `POST /webhook/<hook>`), un dominio +
  un reverse proxy con TLS (Caddy o nginx). Non serve per Discord/Telegram/Slack/Signal, che si
  connettono in uscita.

Crea il tuo file env dal template e inserisci una chiave:

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (consigliato)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

Questo esegue `chimera serve --host 0.0.0.0 --cron`: il gateway HTTP (`/chat`, `/webhook/<hook>`,
`/health`) **più** il daemon cron. Lo stato persiste nel volume `chimera-data`.

**Servire una piattaforma di chat** (Discord nell'esempio) — imposta il token in `.env`, poi
sovrascrivi il comando in `docker-compose.yml`:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

e rilancia `docker compose up -d`. (Telegram/Slack/Signal funzionano allo stesso modo tramite i
loro flag; ognuno richiede il proprio token `CHIMERA_*` corrispondente — vedi `.env.example`.)

**Aggiornare a una nuova versione:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (senza Docker)

Installa in un virtualenv sull'host:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Crea `/etc/systemd/system/chimera.service`:

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

## 3. Pianificare lavoro proattivo (il daemon `--cron`)

`--cron` *esegue* soltanto i job che hai pianificato. Aggiungili con la CLI (persistono in
`CHIMERA_HOME`):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Dentro Docker:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

Il daemon pulsa ogni `--cron-tick` secondi (default 30) e invia l'azione di ogni job attraverso
l'agente quando è dovuta. Un job che fallisce viene registrato e non ferma mai il daemon.

---

## 4. Salute, backup, sicurezza

- **Salute:** `GET /health` restituisce `{"ok": true}`. Compose ha già un healthcheck collegato.
- **Backup:** esegui il backup del volume `chimera-data` (Docker) o della directory `CHIMERA_HOME`
  (systemd) — è tutto lo stato durevole. Esempio:
  `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Segreti:** tieni le chiavi in `.env` (ignorato da git); non incorporarle mai nell'immagine.
- **Esposizione:** vincola il gateway a `0.0.0.0` solo dietro a un firewall/reverse proxy. Imposta
  **`CHIMERA_SERVER_TOKEN`** per richiedere `Authorization: Bearer <token>` sul gateway HTTP e
  sull'API desktop (la UI desktop riceve il token automaticamente solo per i client loopback, così
  un'istanza esposta da remoto resta dietro la propria autenticazione). L'autenticazione è opt-in
  e vuota per default, quindi senza quella variabile non ce n'è nessuna — restringi la porta, o
  esponi solo il percorso del webhook.
- **Sandboxing:** imposta `CHIMERA_SANDBOX=docker` per eseguire i tool shell/codice in un
  container usa-e-getta invece che sull'host.
- **Esecuzione host non presidiata:** dal 2026-07-20 un'esecuzione headless **rifiuta** i comandi
  host sotto il default `CHIMERA_HOST_EXEC=ask` (non c'è un TTY per confermare). Una distribuzione
  che ha davvero bisogno che l'agente esegua shell sull'host imposta deliberatamente
  `CHIMERA_HOST_EXEC=allow`; l'opzione più sicura è `CHIMERA_SANDBOX=docker`, dove il gate viene
  saltato perché il container isola per davvero. Allo stesso modo il server API arma il
  restringimento del taint (`CHIMERA_TAINT_NARROW=1`): dopo che l'agente legge contenuto non
  fidato, i tool di esecuzione/scrittura/uscita falliscono in modo chiuso. Impostalo a `0` per
  continuare ad agire in modo autonomo.

---

## 5. Stato onesto

Chimera è in **alpha**. Questo si distribuisce e gira, e il daemon cron lo rende proattivo — ma
non ha ancora **chilometraggio di produzione**. Inizia con cron a basso rischio, osserva i `logs`,
e tieni presenti le salvaguardie di governance (`--guard` su `solve`, `CHIMERA_SANDBOX=docker`)
per tutto ciò che tocca sistemi reali.

## Dove vengono pubblicate queste pagine

Questi file sono la fonte della documentazione su **chimeraagent.space**, che li renderizza
direttamente da questa directory al momento del build. Modifica il markdown qui e il sito segue;
non c'è una seconda copia da tenere sincronizzata.

La configurazione MkDocs che un tempo viveva in `mkdocs.yml` è stata rimossa. Era completa —
tema, navigazione, dieci pagine — e non è mai stata pubblicata: non c'era un workflow né un branch
`gh-pages`, quindi le istruzioni di deploy che un tempo stavano in questo punto descrivevano un
sito che non esisteva. Una configurazione che nessuno esegue è peggio di nessuna configurazione,
perché la persona successiva modifica la sua navigazione e non riesce a capire perché non cambia
nulla.
