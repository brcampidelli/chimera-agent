---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
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

### Chiedere alla pianificazione quello che non ti sta dicendo

```bash
chimera cron doctor
```

Una pianificazione può ammutolire in due modi, e finché non lo chiedi i due sembrano identici — una
pianificazione senza nulla in scadenza:

- **Non è girato niente.** Il daemon è morto, il container non è mai stato riavviato, l'host ha
  dormito. Nessuna eccezione, nessuna riga di log, nessun verdetto. Ogni altro meccanismo di onestà
  qui sta *a valle di un'esecuzione che è avvenuta*, quindi nessuno di essi ha il suo turno.
- **È girato tutto e ha fallito tutto.** Il daemon è vivo, `last_run` è di un minuto fa, la
  pianificazione avanza — e ogni invio fallisce da un mese. Questo caso si legge come *più sano* del
  primo, perché il campo che sembra indicare salute registra il tentativo, non l'esito.

`cron doctor` pone entrambe le domande e dà consigli diversi, perché le riparazioni non hanno nulla
in comune: in ritardo riguarda il daemon, in fallimento riguarda il job. `chimera cron list` stampa
una riga quando qualcosa sta fallendo, così non devi sapere che il comando esiste per venirlo a
sapere.

**Cosa non è.** È una domanda, non un guardiano: finché il processo è a terra qui non se ne accorge
nessuno, per la stessa ragione per cui un processo andato in crash non può registrare il proprio
crash. Risponde onestamente nel momento in cui qualcosa chiede — una shell, l'app, il prossimo
avvio. Un guardiano vero ha bisogno di un proprio orologio e di una propria vitalità, che è una
decisione a parte ed è
[tracciata come issue #26](https://github.com/brcampidelli/chimera-agent/issues/26). Se vuoi un
avviso invece di una risposta, eseguilo dal cron dell'host stesso:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

Funziona perché è sorvegliato da qualcosa che non è Chimera — che è tutto il punto.

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
  esponi solo il percorso del webhook. Per raggiungere questa istanza dall'app desktop, vedi
  [§5](#5-reaching-this-instance-from-the-desktop-app).
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

## 5. Raggiungere questa istanza dall'app desktop

L'app desktop per default parla con la Chimera che avvia sulla tua macchina. Dalla v0.44 può anche
puntare a una che gestisci tu — questo VPS — e così l'app diventa una finestra sull'agente che passa
già la notte a fare i tuoi job cron.

**Leggi questa parte prima di aprire una porta.** Quello che stai esponendo non è una dashboard.
Ogni schermata di quell'app è una superficie di comando: esegue shell, modifica file, distribuisce
una bacheca di task autonomi e cambia impostazioni. Un'istanza raggiungibile da internet senza token
non è "una Chimera che qualcuno potrebbe guardare" — è una macchina su cui chiunque trovi
l'indirizzo può eseguire comandi, pagati dalle tue chiavi provider.

Devono essere vere tre cose, e senza le prime due l'app si rifiuta di connettersi:

**1 — TLS.** Mettila dietro a un reverse proxy con un certificato vero (Caddy te ne procura uno):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

L'app rifiuta un indirizzo senza `https` fuori dalla tua macchina, perché il token viaggia in un
header `Authorization` a **ogni** richiesta — su http semplice quella è una credenziale consegnata a
ogni salto tra te e il server, e sullo schermo nulla sembrerebbe sbagliato mentre accade.

**2 — Un token.** L'autenticazione è opt-in e vuota per default:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Mettilo nel `.env`, riavvia e incolla lo stesso valore nell'app. L'app rifiuta un indirizzo remoto
senza token per la ragione qui sopra: un'istanza che non ne ha è aperta a chiunque la trovi.

Nota cosa il server deliberatamente **non** fa: quando un client remoto chiede la UI, serve la
pagina *senza* il token. Il token non viene mai consegnato via rete — lo copi nel tuo client, una
volta sola, fuori banda. È per questo che l'app ha un campo apposta.

**3 — L'origine della tua app.** L'app è servita dal proprio sidecar locale, quindi le sue richieste
a questa istanza sono cross-origin e un browser scarta le risposte finché questa istanza non nomina
quell'origine:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

L'app ti mostra il valore esatto quando una connessione fallisce — è nel messaggio di errore, pronto
da copiare. La porta è stabile per installazione (viene ricordata tra un avvio e l'altro dalla
v0.43), quindi si imposta una volta per ogni macchina da cui ti connetti. Più origini si separano
con virgole.

**Questa impostazione non è un confine di sicurezza e non va letta come tale.** CORS decide quale
*pagina* può leggere una risposta; non decide nulla su chi può *chiamare*. Il gate è il token.
Nominare un'origine senza impostare un token non protegge nulla — rende solo un'istanza non protetta
raggiungibile da un browser oltre che da `curl`.

Vuota per default, così un'istanza che nessuno ha configurato si comporta esattamente come prima.

### Cosa ti dice l'app quando fallisce

- **"Il token è stato rifiutato"** — l'indirizzo e l'origine sono giusti; il valore è sbagliato.
- **"Non sono riuscito a raggiungerla"** — o l'indirizzo è sbagliato o l'origine non è permessa. Il
  browser si rifiuta apposta di dire quale dei due, così l'app li nomina entrambi invece di
  indovinare e ti consegna l'origine da permettere.
- **Un avviso di versione** — l'app confronta la versione del proprio backend con questa e dice
  entrambi i numeri. Non rifiuta: un server indietro di una release di solito funziona, e rifiutare
  ti lascerebbe bloccato proprio sulla schermata che ti servirebbe per sistemarlo. Alcuni endpoint
  potrebbero non esistere sul lato più vecchio.

### Ancora più sicuro

Salta del tutto la porta pubblica: raggiungi il VPS via WireGuard o una tailnet Tailscale e punta
l'app all'indirizzo privato. Il token conta comunque — una tailnet è una stanza più piccola, non una
vuota.

---

## 6. Stato onesto

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
