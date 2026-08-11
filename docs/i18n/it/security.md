---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
---

# Sicurezza & salvaguardie

Chimera può eseguire comandi shell, modificare file, chiamare API e modificare le proprie skill.
Include **difesa in profondità**, e — questo conta — la documentazione dichiara dove *si ferma*
ogni livello.

!!! warning "L'unica regola"
    Nessuna di queste salvaguardie sostituisce **l'esecuzione in un ambiente isolato** quando
    concedi autonomia. Il runner `local` di default non è isolato; usa
    `CHIMERA_SANDBOX=docker` (rete disattivata, opzionalmente sotto gVisor) per lavoro non
    fidato.

## I livelli

- **Kernel di governance** — ogni chiamata di tool governata è allow / warn / review / block. Un
  primo filtro economico di firme shell pericolose, non il confine.
- **Sandbox** — un container effimero, senza rete (`CHIMERA_SANDBOX=docker`), irrobustibile con
  gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`).
- **Allowlist di tool per sessione** — concede a un'esecuzione solo i tool di cui ha bisogno; il
  resto viene rimosso interamente dallo schema del modello.
- **Taint tracking** (`--taint`) — il contenuto non fidato è recintato come dato, la sua
  provenienza lo segue in memorie e skill (una skill proveniente da un'esecuzione contaminata
  viene trattenuta per la review), e una volta che un'esecuzione è contaminata i tool pericolosi
  si restringono.
- **Reader in quarantena** — il pattern dual-LLM / CaMeL: il contenuto non fidato è letto da un
  modello senza tool che può solo emettere campi validati da uno schema, così un'injection non
  può produrre una nuova istruzione o chiamata di tool.
- **Monitor cross-agent** — sotto fan-out, un monitor per singolo worker è cieco a un flusso
  *diviso* (un worker recupera contenuto non fidato, un worker diverso lo consuma — il fetch e il
  sink vivono in ledger separati). Un monitor aggregato vede l'intero fan-out; è **sempre attivo**
  per `solve-batch` / `crew-isolated`.

## Fan-out: il monitor cross-agent

Quando più worker che usano tool girano in parallelo (`solve-batch`, `crew-isolated`), ognuno
riceve il proprio ledger di capability, e dopo il batch un monitor aggregato gira su tutti loro.
Cattura pattern che nessun monitor a singolo worker può vedere — l'esfiltrazione divisa in cui il
worker A recupera contenuto non fidato e il worker B lo esegue o lo esfiltra:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Fa sempre e solo **escalation a review** — non blocca mai un'esecuzione — ed è pura
osservabilità (la registrazione non cambia il comportamento). Aggiungi `--taint` sopra per armare
anche l'allowlist adattiva di ogni worker (i tool pericolosi-se-contaminati richiedono allora
l'approvazione).

## Misurato, non affermato

```bash
chimera redteam
```

esegue un corpus di injection attraverso lo stack. Sul corpus integrato, il livello di taint
taglia il **tasso di successo dell'attacco dal 100% al ~14%** — e il report *nomina* ciò che
ancora passa (esfiltrazione tramite un tool consentito) invece di dichiarare 100%.

## Esporre il server HTTP

`chimera serve` si lega a `127.0.0.1` per default. I suoi endpoint che modificano lo stato
(`/chat`, `/a2a`, `/webhook/*`) guidano l'agente, quindi **prima di esporre il server a una rete**,
imposta un bearer token:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Con questo impostato, quegli endpoint POST restituiscono `401` senza un header
`Authorization: Bearer` corrispondente (`GET /health` e l'agent-card A2A restano aperti). Per il
webhook in entrata di WhatsApp, imposta `CHIMERA_WHATSAPP_APP_SECRET` con il secret della tua app
Meta — Chimera verifica quindi l'HMAC `X-Hub-Signature-256` di ogni richiesta e rifiuta un payload
falsificato con `403`. Entrambi sono opt-in (non impostato = nessuna autenticazione, va bene per
localhost); un deployment pubblico dovrebbe impostarli (o stare dietro a un proxy che autentica).

## Limiti onesti

Questo misura se l'azione dannosa di un agente *già injettato* viene fermata — non se il modello
può essere injettato in primo luogo. Il ragionamento libero su prosa non fidata, e l'esfiltrazione
tramite tool legittimamente necessari, restano problemi aperti (tracciati come
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La policy completa e sempre aggiornata vive in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluso
come segnalare una vulnerabilità.
