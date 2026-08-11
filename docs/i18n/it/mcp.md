---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
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
