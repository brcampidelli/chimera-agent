---
source_sha256: 16ac522d3f1fdc1a508d268b9f88b22f75c896bd1ae6dd87839afd256e4d745f
---

# Chimera

Un agente IA open-source (Apache-2.0), auto-evolutivo, il cui nucleo di ragionamento **fonde
più modelli** (panel → giudice → sintetizzatore) dietro un router consapevole dei costi — con un
kernel di governance, una sandbox, e una memoria che impara.

Questo sito è orientato ai compiti: scegli cosa vuoi fare.

<div class="grid cards" markdown>

- **:material-rocket-launch: Inizia subito**
  Installa, aggiungi una chiave, esegui il tuo primo task in cinque minuti.
  [Installazione & primo avvio →](usage.md)

- **:material-toolbox: Fai qualcosa di reale**
  Recipe eseguibili: triage delle email, un brief di ricerca giornaliero, un watchdog di
  repository.
  [Recipes →](recipes.md)

- **:material-power-plug: Connetti tool**
  Collega qualsiasi server MCP (GitHub, filesystem, …).
  [Server MCP →](mcp.md)

- **:material-account-switch: Guida un altro agente**
  Affida un turno a Claude Code o Gemini CLI via ACP — e leggi cosa questo fa alle garanzie.
  [Agenti esterni →](external-agents.md)

- **:material-server: Mettilo in funzione**
  Eseguilo 24/7 su un piccolo server; pianifica job; consegna in chat.
  [Deploy →](deploy.md)

- **:material-shield-lock: Sicurezza**
  Governance, sandbox, taint tracking — e i loro limiti onesti.
  [Sicurezza →](security.md) · [Audit dei canali dormienti →](../../audits/sleeper-channels.md)

- **:material-sitemap: Capiscilo**
  Come si incastrano il nucleo di fusione, l'evoluzione e i livelli di sicurezza.
  [Architettura →](architecture.md)

- **:material-console: Ogni comando**
  Tutti e 79, generati dalla CLI stessa — inclusi i 33 che non comparivano in nessuna
  documentazione.
  [Riferimento dei comandi →](../../commands.md)

</div>

## La riga unica

```bash
uv sync --extra dev && uv run chimera init
```

Poi prova `chimera run "..."`, oppure una recipe vera:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Onesto di default

Chimera è in **alpha**. Include difesa in profondità, ma la documentazione dice chiaramente
dove si ferma ogni salvaguardia — le difese contro l'injection pubblicano persino un numero
misurato (`chimera redteam`). Vedi [Sicurezza](security.md).
