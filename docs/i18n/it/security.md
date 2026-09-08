---
source_sha256: 0f6fea8c584991f0722cb5e5454502c825585f4066f672324c4ab3011ca109dd
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
  Il **comando di verifica** gira nella stessa sandbox della shell dell'agente, e quando quella
  sandbox non è isolata un comando che non hai digitato tu — dedotto dal repository, letto da un
  job cron, da una card o da un workflow — passa per la stessa conferma `CHIMERA_HOST_EXEC`; se
  rifiutato, si astiene invece di eseguire
  (`CHIMERA_VERIFY_NETWORK=1` dà la rete a un verificatore docker; sulle sandbox del kernel non
  può, quindi un verificatore che ha bisogno della rete gira sull'host, e solo se l'hai digitato
  tu).
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

Lo stesso comando stampa il **costo**, cosa che la prima versione di questa pagina non faceva:
senza nessuno a cui chiedere, il restringimento rifiuta il **100% del lavoro legittimo che ha
letto prima qualcosa di esterno** — correggi il file che l'issue nomina, applica l'aggiornamento
che la documentazione descrive — e il gate registrato (over-block ≤ 5%) fallisce. Quel numero non
è un problema di taratura; il gate era vuoto. La modalità di approvazione di default è `ask`, e sul
desktop ora chiede davvero: una chiamata di tool ristretta diventa una domanda a schermo con
allegata la motivazione del ledger, a cui si risponde con un bottone o con `chimera approve`, e che
il silenzio rifiuta dopo `CHIMERA_APPROVAL_WAIT` secondi. Con la persona che approva il lavoro che
ha chiesto lei stessa, l'over-block è 0% e il tasso di blocco degli attacchi non si muove —
misurato, per braccio, in
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
L'esfiltrazione tramite un tool consentito è chiusa dallo stesso cambiamento: l'`http_get` di
un'esecuzione contaminata che porta con sé una query string è una review, e i due GET legittimi con
query string aggiunti al corpus mostrano quanto costa.

### Memoria avvelenata, attraverso le esecuzioni

`redteam` misura una sola esecuzione. L'altra forma è più lenta e non entra in un processo:
l'esecuzione A legge una pagina avvelenata e memorizza ciò che ha "imparato"; l'esecuzione B fa una
domanda non correlata giorni dopo e il richiamo consegna al modello il fatto piantato lì.

```bash
chimera memory-poison
```

Anche questo offline e gratuito. Esegue l'ablazione dei tre livelli che stanno tra quelle due
esecuzioni — il flag di provenienza `tainted`, il gate di ammissione del richiamo, e l'etichetta
`[unverified]` che il fatto indossa entrando nel prompt — perché un numero solo sarebbe compatibile
con uno qualsiasi di loro che non fa nulla. Il risultato in evidenza è ciò che arriva
**senza marcatura**, non ciò che viene bloccato: un fatto avvelenato che porta con sé la propria
origine è un fatto di cui il modello è stato avvertito; uno senza etichetta è indistinguibile da
qualcosa che l'agente ha verificato da sé.

Due risultati della prima esecuzione meritano di essere detti chiaramente, perché nessuno dei due
ci fa bella figura:

- **La configurazione così com'è distribuita fallisce il proprio gate — sul costo.** Marca il 100%
  del veleno e nel farlo distrugge il 25% della memoria onesta. Le vittime hanno un nome: un
  documento di sicurezza che cita un attacco per spiegarlo, e un ticket di supporto che inoltra un
  tentativo. Un pattern matcher sul contenuto non sa distinguere una citazione da un comando.
- **Su questo corpus il gate sul contenuto non aggiunge nulla che l'etichetta di provenienza non
  copra già.** Tutto il suo effetto misurato è la memoria onesta che rimuove. Quindici righe
  scritte a mano sono un indizio e non un verdetto, ed è per questo che sulla loro base non è stato
  cancellato nulla.

Le soglie, il metodo e ciò che i numeri *non* autorizzano a concludere sono in
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
fissati prima della prima esecuzione.

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
