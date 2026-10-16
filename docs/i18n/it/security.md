---
source_sha256: a1dc35531934294a828e1701115cf711421370dd20b71a74eacf70381f310c46
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

## La voce dell'agente nei prompt e nei testi del bot

I prompt e i modelli del bot non devono far affermare all'agente sentimenti, premura, amicizia, una
relazione o promesse su di sé. Di' cosa ha fatto, cosa non è riuscito a fare e quanto è sicuro.
L'incertezza calibrata in prima persona (per esempio «non ne sono sicuro, ma…») è consentita e utile.

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

**L'approvazione, e come si presenta un worker rifiutato.** Ogni worker porta il proprio
approvatore e il proprio registro di ciò che gli è stato permesso fare, quindi un task
rifiutato lo dice invece di riportare `ok`:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

L'`ok` fra parentesi è il verdetto del loop stesso, e il fatto che i due non concordino è
proprio il punto: una chiamata rifiutata torna come una normale riga di osservazione, il
worker la legge come qualunque risultato di tool, prosegue e finisce in prosa. Chi si può
interrogare lo decide `CHIMERA_APPROVAL_MODE` — `deny` rifiuta subito, `ask` chiede su un
terminale e altrimenti annota la domanda per `chimera approve` e aspetta
`CHIMERA_APPROVAL_WAIT` secondi, per domanda e per worker. Il silenzio rifiuta sempre.

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

### Condividere una conversazione con una seconda persona

L'app desktop può condividere una conversazione di codice tramite un **token che apre solo quella
conversazione** — mai il token del server. Il proprietario ne emette uno per ospite
(`POST /api/code/sessions/{id}/share`) e può revocarlo da solo; eliminare la conversazione li revoca
tutti. Un ospite raggiunge quattro rotte sotto `/guest/api/…`: leggere la conversazione, seguirla dal
vivo, inviarvi un messaggio, vedere chi c'è. Nient'altro risponde a un token di condivisione, e l'app
ospite non ha alcuna rotta per rispondere a una scheda di governance — quelle restano sullo schermo del
proprietario.

L'app ospite è anche l'**unica** cosa servita quando il proprietario apre la porta di rete
(`POST /api/code/share/network`, richiusa con `DELETE`, mai aperta all'avvio): una macchina sulla LAN
raggiunge quelle quattro rotte e nulla di ciò che il token del server protegge. Detto chiaramente prima
di consegnare un link: un ospite può chiedere all'agente qualsiasi cosa chiederebbe il proprietario, nel
progetto del proprietario, con i suoi strumenti e la sua spesa — sotto le cuciture predefinite della
richiesta, e senza approvare schede.

## Il backend dell'app desktop

Il backend dell'app ascolta in loopback, e loopback non è una persona: qualsiasi processo della
macchina raggiunge la porta.

- **Un token su ogni rotta.** L'app desktop avvia sempre il suo backend con un bearer token — un
  segreto nuovo a ogni avvio, oppure il `CHIMERA_SERVER_TOKEN` che il proprietario ha già impostato.
  Ogni rotta `/api/…` risponde allora `401` senza di esso, `/api/health` compresa. Restano aperti solo
  i file statici dell'app e l'app ospite (`/guest/api/…`, che controlla i propri token di
  condivisione); il bridge MCP (`/api/bridge/…`) mantiene il proprio token. La shell consegna il token
  di avvio al backend nell'ambiente e alla finestra tramite lo script di inizializzazione della
  webview — mai su una riga di comando, in un URL o in un file. Ciò che il backend avvia non lo
  eredita: l'ambiente dei figli del sandbox e quello con cui girano `gh` e `git` scartano ogni
  variabile con nome da segreto (tranne `GH_TOKEN` e le altre credenziali proprie di gh).
- **Approvare senza il codice richiede lo schermo.** Un token dice chi può chiamare l'API, non che il
  proprietario stia guardando. Un sì senza codice (`POST /api/approvals/{id}`) riesce solo quando la
  richiesta porta anche la credenziale di schermo in `X-Chimera-Screen`, accanto al bearer token. Viene
  estratta a ogni avvio e tenuta in memoria: mai in un file, un record, un log, il `.env` o la
  risposta di una rotta, mai uguale al bearer token, e rimossa dall'ambiente appena letta (la shell la
  passa come `CHIMERA_SCREEN_TOKEN`). Qualsiasi token letto dal disco — `service.token`, un
  `CHIMERA_SERVER_TOKEN` nel `.env` — ha ancora bisogno del codice per domanda per un sì; un rifiuto
  non richiede codice. `chimera service` non detiene alcuna credenziale di schermo, quindi lì ogni sì
  chiede il codice. `chimera app` in un browser la mette nella pagina che serve a una richiesta in
  loopback (`<meta name="chimera-screen">`), esattamente dove e quando quella pagina porta già il
  bearer token — la stessa esposizione, per decisione del proprietario del 16/10/2026.
- **Il servizio in background.** `chimera service start` esegue lo stesso backend senza finestra.
  Pubblica `~/.chimera/service-<channel>.json` (0600, scritto in modo atomico; `CHIMERA_SERVICE_DIR`
  lo sposta) con il token persistente del proprietario (`<home>/service.token`, oppure un
  `CHIMERA_SERVER_TOKEN` scelto dal proprietario), e un client si fida di quel file solo dopo che
  `GET /api/info` risponde con lo stesso pid, versione, id e cartella dati. Quel token apre l'intera
  API, quindi resta separato da quello del bridge: nulla sul lato MCP legge i file del servizio, e il
  bridge li rifiuta come file. Il record di avvio dell'app installata,
  `~/.chimera/desktop-install.json`, contiene solo quattro variabili di percorso — `CHIMERA_HOME`,
  `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —, chi lo scrive rifiuta qualsiasi
  altro nome, e `start` lo valida prima di eseguire qualunque cosa a partire da esso.

## Cosa decide il proprietario, e cosa un repository non può

- **Policy di distribuzione, solo restrittiva.** `<CHIMERA_HOME>/policy.json` (o il file indicato da
  `CHIMERA_POLICY_FILE`) contiene regole ordinate `provider.use` / `integration.use` / `tool.use`;
  decide l'ultima che corrisponde. Un `allow` lì non concede mai nulla: annulla solo un deny
  precedente dello stesso file, quindi non può riaprire `CHIMERA_TOOL_DENYLIST`, il pavimento di
  portata né un BLOCK del kernel. Un file non valido non viene mai applicato in parte: `serve`,
  `chat`, `assist`, `tui` e `acp` si rifiutano di avviarsi, `doctor` fallisce, e il desktop si avvia ma
  rifiuta ogni strumento finché non viene corretto. L'agente non può riscriverlo — gli strumenti di
  scrittura rifiutano il file ovunque si trovi, e il bridge non può puntarlo altrove.
- **"Sempre in questo progetto".** Su una scheda di approvazione il proprietario può ricordare un sì
  per un progetto: lo strumento, oppure, per un comando di shell, le sue prime due parole. Risponde poi
  alle domande che corrispondono, **anche dopo che l'esecuzione ha letto contenuto non fidato** —
  decisione del proprietario del 15/10/2026, sapendo che una pagina ostile potrebbe guidare un comando
  dentro quel prefisso. Salta soltanto la domanda: un BLOCK del kernel e il pavimento di portata
  vincono ancora, e un comando che concatena, usa una pipe o reindirizza (`;`, `&&`, `|`, `>`, `$(`…)
  non corrisponde mai. Solo il proprietario ne crea o ne revoca uno; il bridge no.
- **Riferimenti con nome.** Le cartelle che il proprietario elenca in `<CHIMERA_HOME>/references.json`
  sono raggiungibili come `@alias/path` solo dagli strumenti di lettura (`read_file`, `list_dir`,
  `grep`, `glob`); ogni strumento di scrittura rifiuta un alias. Una radice del filesystem, la home
  dell'utente o la cartella dati vengono rifiutate come riferimento. Solo le superfici presidiate li
  ricevono — `chat`, `assist`, `tui`, un `solve` presidiato, il turno Code del desktop (mai quello di
  un ospite) —, quindi bot, cron e `scenarios` non ne hanno nessuno.
- **Agenti in Markdown.** `.chimera/agents/*.md` in un repository è testo di qualcun altro, quindi può
  solo restringere: i suoi strumenti vengono intersecati con quelli governati della sessione, i suoi
  passi limitati da quelli della sessione, le chiavi sconosciute rifiutate, il suo `model` ignorato, e
  il suo corpo viene recintato come dato non fidato. Solo i file del proprietario in
  `<CHIMERA_HOME>/agents` possono fissare un modello del catalogo.
- **Chat collegate a conversazioni.** `/link` in Discord o Telegram fa sì che una chat prosegua una
  conversazione Code, e solo il proprietario elencato nella allowlist di quel bot può collegarne una —
  senza allowlist, nessuno. Il testo di uno sconosciuto in una stanza collegata non raggiunge mai la
  conversazione, e i media vengono rifiutati invece di essere inoltrati. Il turno gira sul pavimento
  del bot, non su quello del desktop: solo i server MCP di `CHIMERA_BOT_MCP_SERVERS`, nessun connettore
  OpenAPI, né concessione di shell né host exec.
- **I connettori del proprietario restano lontani dalle porte che uno sconosciuto può aprire.** Un
  connettore OpenAPI chiama all'esterno con la chiave del proprietario, quindi risponde a chi guida
  l'esecuzione. Non si carica su una superficie di messaggistica finché un bot configurato è senza
  allowlist, mai in un job di issue di GitHub (chiunque scrive l'issue; si controlla solo il
  repository), e su `/v1/chat/completions` solo finché `CHIMERA_SERVER_TOKEN` è impostato. Un modulo,
  `chimera/governance/surfaces.py`, decide che tipo di superficie è un nome, in base alla sua famiglia
  (`platform:discord` è un bot); un test fallisce se un modulo torna a scrivere la propria lista.

## Limiti onesti

Questo misura se l'azione dannosa di un agente *già injettato* viene fermata — non se il modello
può essere injettato in primo luogo. Il ragionamento libero su prosa non fidata, e l'esfiltrazione
tramite tool legittimamente necessari, restano problemi aperti (tracciati come
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La policy completa e sempre aggiornata vive in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluso
come segnalare una vulnerabilità.
