---
source_sha256: da279a713d209b2b6f4e14d01cd9d7fddcf07586511cb7c418edb791265112d5
---

# Connettere server MCP

MCP (Model Context Protocol) è il modo standard per collegare tool esterni a un agente —
GitHub, filesystem, Notion, database, e centinaia di altri server lo parlano. Chimera ha un
client MCP di prima classe: i tool di qualsiasi server diventano ordinari tool di Chimera,
sedendo nello stesso registro di quelli built-in, governati dagli stessi livelli di
allowlist/kernel/ledger.

## Installa l'extra del client

Il client MCP vive dietro un extra opzionale così il nucleo resta leggero:

```bash
uv sync --extra mcp
```

La maggior parte dei server sono pacchetti Node, quindi ti serve anche `npx` (incluso con
Node.js).

## Smoke test di 60 secondi (senza credenziali)

Il server filesystem di riferimento non richiede alcun token — espone semplicemente tool di
lettura/scrittura su una directory che scegli tu:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Passa quel registro a un `Agent` (o guarda `examples/mcp_github.py` per il loop completo) e il
modello può ora chiamare i tool del server come qualsiasi altro.

## Un server vero: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

Questa è l'intera integrazione: ~26 tool GitHub (cercare repository, leggere file, elencare
issue, creare PR, ...) compaiono nel registro. Versione eseguibile end-to-end:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Come si incastra nei livelli di sicurezza

I tool MCP sono ordinari oggetti `Tool`, quindi tutto si compone:

- **Allowlist per sessione** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  concede solo i tool MCP di cui questa esecuzione ha bisogno; quelli non concessi non
  raggiungono mai il modello.
- **Kernel di governance** — `govern_registry(...)` regola le chiamate MCP con
  allow/warn/review/block come qualsiasi comando shell.
- **Ledger di taint** — avvolgi con `ledger_registry(...)` e i fetch MCP vengono registrati; nota
  che oggi solo i tool nominati in `FETCH_TOOLS` sono auto-classificati, quindi tratta il
  contenuto MCP come non fidato e preferisci girare con la semantica `--taint --guard` quando il
  server recupera dati esterni.
- **`instructions` del server** — il testo che un server restituisce a `initialize` viene scartato,
  per scelta: è testo di server non fidato, e nulla lo marca come dato come avviene per una lettura
  recintata. Il prezzo è che le indicazioni d'uso di un server non raggiungono mai il modello; un
  host che le passa non dovrebbe comunque contarci (arXiv 2608.08467: con uno strumento di ricerca
  disponibile, 9 modelli su 24 sono scesi sotto il 15% sulle consultazioni messe nelle istruzioni
  del server). Passarle come dati recintati sotto taint è aperto, non fatto. Scartarle **non** è un confine contro il testo scritto dal server: nomi e
  descrizioni degli strumenti dello stesso server raggiungono il modello come il server li ha
  scritti, senza recinto; un server che colleghi è un server di cui il modello legge le parole.
- **Fissaggio del manifest** — la prima volta che un server viene montato, nomi, descrizioni e
  input schema dei suoi strumenti vengono memorizzati in `mcp_pins.json`, accanto a `mcp.json`. Se
  un montaggio successivo elenca qualcosa di diverso, il server viene **trattenuto**: l'app,
  `chimera serve` e i suoi bot (tutto ciò che monta tramite il pool MCP condiviso) non lo montano
  finché non approvi la modifica, con il testo vecchio e quello nuovo in vista, tramite `chimera mcp
  approve NOME` o la schermata MCP. `chimera mcp list` indica i server trattenuti. Gli strumenti
  montati sono l'elenco che è stato controllato, quindi un server non può rispondere al controllo
  con un testo e al modello con un altro. Aggiungere o rimuovere il server con `chimera mcp
  add/remove` o dall'app ne dimentica il fissaggio (il montaggio successivo torna a essere il primo
  contatto). Il fissaggio è fiducia al primo uso: coglie una descrizione che **cambia**, non una che
  era ostile fin dall'inizio.
  **Non coperta:** l'API Python qui sopra. `connect_stdio` monta ciò che il server elenca, e lo stesso
  fa `autoload_into_registry` a meno che tu non gli passi `mcp_path`, il file di cui deve controllare
  i fissaggi.
- **Segnali di selezione** — `chimera mcp test` e la schermata MCP annotano gli strumenti con le frasi
  che cercano di orientare quale strumento sceglie il modello ("always use this tool", "do not use
  other tools", "ignore previous instructions", `<IMPORTANT>`), lette nella descrizione e in ogni
  descrizione di parametro. Solo un'annotazione: non rifiuta nulla, e quanto spesso scatti su server
  onesti non è stato misurato.

## Chimera *come* server MCP

Il client sopra permette a Chimera di chiamare altri tool. Vale anche il contrario: esegui
Chimera **come** un server MCP così qualsiasi client MCP — Claude Desktop, un IDE, un altro
agente — può chiamare l'intero motore come tre tool.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Espone:

| Tool | Cosa fa |
| --- | --- |
| `chimera_solve` | Risolve un task in autonomia con piano + verifica-o-ripristina; restituisce la risposta. |
| `chimera_fuse` | Risponde a un prompt attraverso il motore LLM-Fusion (panel → giudice → sintetizzatore). |
| `chimera_memory_search` | Cerca nella memoria a lungo termine di Chimera e restituisce i fatti principali. |

Punta un client MCP verso di esso come server stdio. Per Claude Desktop, aggiungi alla sua
configurazione:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` richiede una chiave provider per `chimera_solve`/`chimera_fuse` (la ricerca in memoria
funziona senza). Aggiungi `--fuse` per instradare i turni profondi del solver attraverso la
fusione, `--no-memory` per saltare il recall. Poiché stdio è il canale, tutti i log vanno su
stderr — stdout trasporta solo il protocollo.

## Lasciare che Claude usi l'app desktop

`chimera serve --mcp` costruisce un proprio agente. `chimera mcp desktop` non costruisce nulla: è
un telecomando per l'app desktop **che hai già aperta**, quindi Claude vede le stesse
conversazioni, esecuzioni e approvazioni che vedi tu, e ciò che avvia gira sotto la governance
dell'app, sulle sue schermate.

1. Nell'app, apri **Impostazioni → Claude** e attiva **Consenti a Claude di usare questa app**.
2. Registra il server in Claude Code (o aggiungi lo stesso comando alla config di Claude Desktop):

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

Con il primo interruttore attivo, Claude può leggere e avviare conversazioni (`desktop_send`),
esecuzioni, batch, bacheche e job cron, cercare e modificare la memoria, e leggere file e stato
git. Un'esecuzione che avvia porta la postura che hai configurato, e una richiesta che prova ad
allargarla — un comando `verify`, esecuzione sull'host, un altro agente, auto-approvazione — viene
rifiutata. Le approvazioni restano a te: `desktop_approvals` si limita a elencarle. Quando un
turno si ferma su una, `desktop_send` risponde subito che ti sta aspettando, e il turno prosegue
nell'app; `desktop_job` dice come finisce.

Il secondo interruttore, **Controllo totale**, aggiunge `desktop_approve` (rispondere ad
approvazioni e passi con cancello) e `desktop_settings` (modificare le impostazioni e l'identità
dell'agente). Con esso attivo, Claude può approvare azioni senza di te — e una pagina o un
messaggio con prompt injection letto dall'agente potrebbe indurlo a farlo. Questi due strumenti
non compaiono affatto nell'elenco finché è spento, e l'app li rifiuta se vengono chiamati
comunque.

Alcune decisioni restano tue, qualunque interruttore sia acceso. Quale modello risponde — ogni
impostazione di modello, la catena di riserva, il panel, il giudice e il sintetizzatore della
fusione, la modalità di costo, la cascata e le risposte verificate — e se l'app esegue i lavori
pianificati, Claude può solo *suggerirlo*: non viene scritto nulla, e l'app ti mostra una scheda
con il valore attuale e quello proposto di ogni impostazione, da approvare o rifiutare lì. Claude
non può rispondere a quella scheda per nessuna via; se l'impostazione è cambiata prima della tua
approvazione, non viene applicato nulla. E concedere i comandi a una cartella, eseguire un comando
nel Runner, avviare un bot di messaggistica e salvare un agente con i suoi permessi sugli strumenti
sono rifiutati del tutto attraverso il ponte: li fai tu nell'app.

E un'esecuzione avviata da Claude, con qualunque interruttore, usa i modelli che hai configurato e
non arriva più lontano della postura che hai configurato. Una richiesta che indica un modello, un
piano dei ruoli, un profilo, un panel di fusione o un altro agente viene rifiutata; così come una
postura più ampia — più portata, approvazioni più lasche, esecuzione sull'host o un comando `verify`
dove non hai concesso la shell, o l'approvazione automatica. Chiedere a un'esecuzione di fare meno
(sola lettura, o approvare sempre) è consentito.

Ciò che nessun interruttore consente: leggere o scrivere una chiave API, un token o un webhook.
Le modifiche alle impostazioni rifiutano i nomi di credenziali, le rotte che portano chiavi o
link di condivisione non sono raggiungibili, i file di credenziali (`.env`, chiavi private) non
si possono leggere, scrivere né cercare, e ogni risultato viene ripulito dai valori delle
credenziali. Non si può nemmeno puntare un workspace alla cartella dati dell'app, né a una
cartella che la contiene (la tua cartella home, per esempio): lì sono custodite le risposte alle
approvazioni, e un file scritto lì ne risponderebbe una.

Come si collega: finché l'interruttore è attivo, l'app scrive `~/.chimera/desktop-bridge.json`
(l'URL della sua API in loopback e un token casuale; su POSIX leggibile solo da te, su Windows
dentro il tuo profilo). Spegnere l'interruttore o chiudere l'app lo cancella e ritira il token.
Ad app chiusa, ogni strumento risponde "Chimera desktop is not running, or 'Allow Claude to
operate this app' is off in Settings." Claude elenca gli strumenti al collegamento; dopo aver
acceso o spento **Controllo totale**, ricollega il server (`/mcp` in Claude Code) per vedere il
nuovo elenco.

## Parlare A2A (agente → agente)

MCP connette gli agenti a dei *tool*; **A2A** (Agent2Agent, Linux Foundation) connette gli
agenti *tra loro* — è nativo in LangGraph, CrewAI e AutoGen. Chimera lo parla anch'esso, così un
orchestratore LangGraph/CrewAI può delegare un task a Chimera e ricevere indietro un risultato
completato.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` aggiunge due rotte al server HTTP:

| Rotta | Scopo |
| --- | --- |
| `GET /.well-known/agent.json` | L'Agent Card — identità + skill pubblicizzate (solve, fuse). |
| `POST /a2a` | Ciclo di vita del task JSON-RPC 2.0: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Un client invia `message/send` con una parte testuale; Chimera esegue l'agente autonomo e
restituisce un task `completed` (o `failed`) che trasporta la risposta come messaggio
dell'agente. Oppure invia `message/stream` e ottiene uno stream di **Server-Sent Events**: prima
il task in stato `working`, poi il task `completed`/`failed` una volta terminata l'esecuzione —
così un orchestratore vede il progresso senza fare polling. L'agent card pubblicizza
`capabilities.streaming: true`.

**Ambito, onestamente:** lo stream attualmente emette due eventi (working → final), non delta di
token per singolo passo, e le push notification non sono implementate. È uno stream conforme,
privo di necessità di polling — sufficiente per essere un nodo streamabile di prima classe in
un'app LangGraph/CrewAI.

## Risoluzione dei problemi

- `TimeoutError: MCP server ... did not become ready` — il comando non è partito. Esegui la
  stessa riga `npx ...` manualmente in un terminale per vedere il suo errore (token mancante,
  Node mancante, download del pacchetto al primo avvio lento — aumenta `connect_timeout`).
- `ModuleNotFoundError: mcp` — installa l'extra: `uv sync --extra mcp`.
- Collisioni di nomi di tool — passa sempre un `name_prefix`.
- La sessione esegue il server come sottoprocesso per tutta la vita del tuo script; chiama il
  `close()` della sessione del `connector` (o lascia semplicemente terminare il processo) per
  smontarlo.
