---
source_sha256: 278a01e5a149ebc25fc893fdd255f56769e2701bc0134bf7b6e8c0b87bf07e57
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
- **Sandbox** — un container effimero senza rete (`CHIMERA_SANDBOX=docker`), rafforzabile con
  gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). Il **comando verify** viene eseguito nello stesso
  sandbox della shell dell’agente. Un verificatore dichiarato in `.chimera/verify.toml` resta un
  comando shell: su un host non isolato viene eseguito solo se il progetto ha una concessione
  `shell` attiva e `CHIMERA_HOST_EXEC` non vieta l’esecuzione sull’host. Senza tale concessione si
  astiene; i comandi dedotti dai file di build continuano a passare dalla conferma
  `CHIMERA_HOST_EXEC` e si astengono se viene rifiutata. Una concessione del progetto autorizza il
  comando configurato, ma non rende isolato il sandbox dell’host (`CHIMERA_VERIFY_NETWORK=1` dà
  rete a un verificatore Docker; i sandbox del kernel non possono farlo, quindi un verificatore
  che richiede la rete viene eseguito sull’host solo se autorizzato altrimenti). Tratta la
  configurazione e il suo comando come contenuto eseguibile del progetto; esaminali prima di
  concedere l’accesso alla shell. Se un turno modifica il verificatore configurato, la verifica si
  astiene; il comando viene acquisito prima del turno e richiede, prima della prima esecuzione,
  un’approvazione durevole del proprietario legata al suo hash SHA-256 esatto (e di nuovo a ogni
  modifica). Questa approvazione vincola solo la stringa del comando, non i suoi effetti: `pytest`,
  `make test` e `npm test` eseguono file controllati dal progetto, come `conftest.py`, le ricette
  del `Makefile` e gli script del pacchetto, che l’agente potrebbe aver modificato.
Una chiave `setup` opzionale in questo file indica un comando per preparare l’ambiente del progetto. Richiede una propria approvazione del proprietario legata all’hash esatto e la stessa concessione `shell` attiva del progetto. Viene eseguito al massimo una volta per workspace e hash del comando; gli errori vengono comunicati all’agente senza interrompere il turno. Le approvazioni e il contrassegno di preparazione sono conservati nella home di Chimera, non nel checkout. Le concessioni e gli hash approvati vengono ereditati solo dai worktree durevoli creati da Chimera per quel progetto registrato; i worktree Git arbitrari non li ereditano. Il registro dei worktree dell’app è conservato sotto `CHIMERA_HOME`. Un processo che ha già accesso shell all’host può scrivervi, così come può modificare il registro dei progetti.
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

### Il tuo telefono o browser: associare un dispositivo

Un link ospite è per qualcun altro. Per il proprietario sul proprio telefono c'è l'associazione:
`chimera pair` (o **Associa un dispositivo** in Impostazioni › Accessi) stampa un codice che funziona
**una volta, per cinque minuti**, con un QR dell'indirizzo di associazione. Si digita in una pagina
servita da un secondo listener sulla LAN — chiuso a ogni avvio e aperto solo dal proprietario
(`chimera pair --lan`, o **Apri** sulla scheda) — e diventa un cookie di 30 giorni (`HttpOnly`,
`SameSite=Strict`, `Secure` su https) firmato con una chiave derivata dal segreto dei dispositivi
del proprietario (`<home>/devices.key`). Cinque codici sbagliati annullano ogni codice ancora
valido; cinque da uno stesso indirizzo escludono quell'indirizzo per quindici minuti.

Un dispositivo associato è il proprietario **per leggere e parlare**: conversazioni, turni,
indicazioni, risposte alle domande dell'agente stesso e il rifiuto di un'approvazione. Non è lo
schermo del proprietario. Approvare richiede ancora il codice di ogni domanda (studio 30, S30-30), e
le rotte che concedono accessi, cambiano impostazioni, autorizzano cartelle, avviano bot o fermano il
servizio rispondono `403` a un dispositivo. La scheda Accessi elenca ogni dispositivo con l'ultimo
uso e la rete (`192.168.1.x`); **Revoca** ne chiude uno subito, compreso uno stream dal vivo che ha
aperto, e **Revoca tutti i dispositivi** sostituisce il segreto, così nessun cookie emesso prima
viene più verificato. Il bridge del desktop non può associare, emettere un codice, elencare né
revocare.

### Una scheda del browser come schermo del proprietario: associazione con codice

Una scheda sull'URL del servizio non ha una credenziale di schermo, quindi un'azione riservata al proprietario (una concessione, un sì che richiede lo schermo, un interruttore riservato al proprietario) fallirebbe lì in silenzio. L'app desktop mostra un **codice di associazione del browser** (Impostazioni › Sicurezza › Associazione del browser, **Mostra codice di associazione**); la scheda lo chiede e il server lo scambia con un cookie HttpOnly e SameSite=Strict che rende quella scheda lo schermo del proprietario per la sua sessione (12 ore; `CHIMERA_BROWSER_SCREEN_SECONDS` la regola tra cinque minuti e un giorno).

- **Solo lo schermo desktop del proprietario emette un codice.** Il filtro (`chimera/api/browser_screen.py`, `desktop_screen_refusal`) respinge il bridge, un dispositivo associato, una sessione del browser e uno schermo che la pagina stessa offre (`chimera app` in un browser, la cui credenziale può essere letta dalla shell dell'agente). Un token bearer da solo non emette mai. Lo stesso filtro elenca e revoca le sessioni.
- **Un codice funziona una volta sola, per cinque minuti**, e ne è attivo uno solo alla volta. Cinque tentativi errati lo annullano. Codici sbagliati, scaduti e già usati ricevono la stessa risposta. È memorizzato come HMAC con chiave, mai in chiaro.
- **Una sessione è solo l'interruttore dello schermo e nient'altro.** Non può emettere, elencare o revocare sessioni, né attivare «Ignora permessi», il bridge desktop (`CHIMERA_DESKTOP_BRIDGE`) o il suo controllo totale. Solo lo schermo desktop del proprietario può farlo; disattivarli resta consentito, perché così si restringe soltanto.
- **Il magazzino non si può falsificare.** Il file (`<home>/browser-screen.json`) contiene `HMAC(chiave, segreto)`, e la chiave esiste solo nella memoria del processo che l'ha creata: una voce scritta a mano o copiata non verifica quindi nulla. Il file è nell'elenco dei file di governance, così la shell dell'agente non può riscriverlo.
- **Una sessione associata è valida solo sul processo che l'ha emessa.** Il suo digest è firmato con la chiave di quel processo: un altro processo che legge lo stesso home non accetta il cookie, e la sessione termina al riavvio di quel processo. Se l'app desktop e `chimera serve` condividono un home, associa contro l'istanza che stai usando.
- **Ogni modifica passa dal file sotto un blocco esclusivo**, rileggendolo prima. Una revoca fatta da un processo vale anche rispetto a una scrittura successiva di un altro processo, e una verifica legge sempre il file com'è adesso.
- **Una scrittura deve provenire dall'origine di questo server.** Su un metodo non sicuro (POST, PUT, PATCH, DELETE), il cookie vale solo se `Sec-Fetch-Site` è `same-origin` o se `Origin` coincide esattamente con schema e host di questo server. Un'altra porta di localhost, una pagina di un altro sito o uno script senza nessuna delle due intestazioni vengono rifiutati.
- **L'associazione non ha un contatore per indirizzo condiviso.** L'unico limite sono i cinque tentativi errati del codice attivo. Un processo locale non può chiudere fuori il proprietario per un quarto d'ora; il proprietario genera un nuovo codice.
- **Termina alla richiesta successiva** alla revoca (**Revoca** o **Revoca tutte**) o alla scadenza, verificata a ogni richiesta.
- **Nulla di segreto viene scritto dopo la creazione**: né il codice né il segreto, in nessuna risposta oltre a quella che li ha creati, in nessuna riga di log e nel registro di audit (`audit.jsonl`), che registra ogni emissione, scambio, scambio rifiutato e revoca per azione e identificativo di sessione.
- Un dispositivo non è mai uno schermo. La porta dei dispositivi rifiuta ogni percorso `/api/security/`, e lo scambio rifiuta un dispositivo senza eccezioni.

Fuori casa, Chimera **non gestisce alcun relay proprio**. Se vuoi raggiungere il listener dei
dispositivi dall'esterno, mettigli davanti un tunnel che controlli tu —
[Tailscale](https://tailscale.com/) su entrambi i dispositivi, o un [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
verso `http://127.0.0.1:<port>` — e decidilo consapevolmente: estende la stessa porta fin dove
arriva il tunnel. Non collegare mai l'app stessa a `0.0.0.0` per questo.

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

### Controllo completo tramite il ponte

Il ponte del desktop (`chimera mcp desktop`) ha tre interruttori, ognuno senza effetto senza il
precedente: **Consenti a Claude di usare questa app**, **Controllo completo** e, dalla decisione del
proprietario del 2026-10-18, **Controllo completo tramite il ponte**, in Impostazioni > Sicurezza.

- **Solo lo schermo lo attiva.** `CHIMERA_BRIDGE_FULL_CONTROL` non è nell'elenco delle impostazioni
  modificabili: `PATCH /api/config` lo rifiuta a chiunque e il `settings.edit` del ponte lo rifiuta per
  nome. La sua unica rotta, `POST /api/security/bridge-full-control`, rifiuta ogni richiesta inoltrata
  dal ponte e lo attiva solo con la credenziale dello schermo; il token del proprietario da solo è
  `service.token`, leggibile su disco. Lo schermo chiede conferma. Disattivarlo non richiede lo schermo:
  restringe soltanto.
- **Cosa aggiunge.** Le decisioni del proprietario (consentire comandi in una cartella, salvare un agente
  con i suoi strumenti, regole di approvazione ricordate, rifiutare tutto, ricordare un progetto,
  rispondere a un suggerimento di impostazione, plugin e marketplace, pulizia dei worktree), le rotte
  che richiedono il `confirm: true` della persona (ripristinare, eliminare ed esportare un design, le
  pulizie dello spazio, riavviare e reimpostare), le impostazioni del proprietario — postura,
  protezioni, approvazioni, limiti, modelli e pianificazione, scritte invece che suggerite — e aprire
  una schermata dell'app (`ui.navigate`, un indirizzo verificato contro le schermate dell'app, mai un
  URL).
- **Cosa non aggiunge mai.** Le credenziali: nessuna chiave, token, password o segreto OAuth viene letto
  o scritto; il Runner, aggiungere un server MCP o accedervi restano del proprietario, e il vault delle
  chiavi, la lettura del `.env` da parte dell'agente e l'URL base del provider non sono scrivibili. E
  chi raggiunge l'app: associare dispositivi, la condivisione, la porta LAN, il token del server, i bot e
  le loro liste di accesso.
- **Il kernel decide per primo.** Un'approvazione risponde a un REVIEW; un BLOCK del kernel non arriva
  mai a chi approva. I controlli per esecuzione restano: un'esecuzione avviata dal ponte usa i modelli e
  la postura configurati.
- **Visibile e registrato.** Finché è attivo, ogni schermata mostra una barra con **Disattiva** in un
  clic, e ogni chiamata fatta con questa autorità scrive una voce di audit (`via: "bridge"`,
  `scope: "full_control"`, la rotta, lo stato e gli id — mai un corpo o un valore), elencata nella
  scheda Sicurezza.
- **Limite onesto.** L'interruttore vive in `.env`, come gli altri due: un processo che può scrivere quel
  file lo attiva senza lo schermo. Gli strumenti dell'agente e il ponte rifiutano il file; il resto di ciò
  che gira come il proprietario, no.

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
  non corrisponde mai. Solo il proprietario ne crea o ne revoca uno; il bridge no, a meno che il
  proprietario non abbia attivato il controllo completo tramite il ponte (sopra).
- **Riferimenti con nome.** Le cartelle che il proprietario elenca in `<CHIMERA_HOME>/references.json`
  sono raggiungibili come `@alias/path` solo dagli strumenti di lettura (`read_file`, `list_dir`,
  `grep`, `glob`); ogni strumento di scrittura rifiuta un alias. Una radice del filesystem, la home
  dell'utente o la cartella dati vengono rifiutate come riferimento. Solo le superfici presidiate li
  ricevono — `chat`, `assist`, `tui`, un `solve` presidiato, il turno Code del desktop (mai quello di
  un ospite) —, quindi bot, cron e `scenarios` non ne hanno nessuno.
- **`.worktreeinclude` e la pulizia dei worktree archiviati.** Il `.worktreeinclude` di un repository (o `.chimera/worktreeinclude`) nomina file ignorati — `.env`, un database locale — da copiare nel worktree di ogni nuova conversazione. Oltre a ciò che la base del repository contiene già, è l'unica cosa che un nuovo worktree eredita, ed è testo del repository stesso, quindi non viene copiato nulla finché lo schermo del proprietario non acconsente: una volta per progetto e di nuovo a ogni modifica del file. Lo schermo mostra cosa verrebbe copiato; un client del bridge o un dispositivo associato può solo far comparire la richiesta. Un link simbolico o una junction non viene mai seguito, la copia è limitata a 50 MB e 200 file, e ogni copia viene verificata con git dentro il worktree: un file che git lì non ignorerebbe viene cancellato di nuovo, così `git add -A` non può metterlo in stage. `CHIMERA_WORKTREE_CLEANUP` rimuove il worktree di una conversazione archiviata circa trenta giorni dopo, contati dal più tardivo tra archiviazione, ultima apertura e giorno in cui l'interruttore è stato visto attivo per la prima volta. Attivarlo cancella cartelle, quindi spetta solo allo schermo del proprietario, mai al bridge. Senza supervisione, conserva ogni worktree che anche il dialogo di pulizia del proprietario conserverebbe (modifiche non committate, commit non inviati, uno che Chimera non ha creato) e anche ogni worktree con un file ignorato che `.worktreeinclude` non ha copiato, perché `git status` non li elenca.
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

**Il ciclo di chiusura fa push da solo.** Quando la verifica di un turno di codice passa e il suo controllo del diff è pulito, Chimera fa commit delle modifiche del turno sul branch dello spazio di lavoro e invia quel branch a `origin` per aprire una pull request. Non invia mai al branch predefinito e non forza mai un push. Esegue il merge solo se il proprietario ha attivato `CHIMERA_CLOSE_LOOP_MERGE`, e in quel caso mai una pull request che tocchi fatturazione, una migrazione distruttiva, RLS, un grant o un segreto. La avvia solo la schermata del proprietario; il bridge, i canali e il lavoro in background no.

## Limiti onesti

Questo misura se l'azione dannosa di un agente *già injettato* viene fermata — non se il modello
può essere injettato in primo luogo. Il ragionamento libero su prosa non fidata, e l'esfiltrazione
tramite tool legittimamente necessari, restano problemi aperti (tracciati come
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La policy completa e sempre aggiornata vive in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluso
come segnalare una vulnerabilità.

## Limiti dell’elusione dei permessi di sessione

L’elusione facoltativa dei permessi della sessione Code è autenticata con una chiave HMAC generata nella memoria del processo; la chiave non viene mai scritta su disco né ereditata dai processi figli. I flag `bypass_permissions` persistiti e le attestazioni `via_screen` accodate senza un HMAC valido vengono ignorati. La scelta dei permessi dura quindi solo fino al riavvio del processo dell’app/servizio, dopodiché l’elusione è disattivata per impostazione predefinita. Una shell può comunque modificare il JSON, ma non falsificare l’attestazione. Decisione del proprietario (26/10): con l’interruttore attivo, l’agente sblocca tutto ciò per cui altrimenti chiederebbe conferma — git e i push verso GitHub, `gh`, gli strumenti dei connettori MCP, i deploy, i caricamenti, le scritture HTTP e gli strumenti di invio, sia in un’esecuzione pulita sia in una contaminata. Tre cose chiedono ancora al proprietario, e nient’altro: (a) le regole BLOCK del kernel e la denylist distruttiva (`rm -rf /`, `mkfs`, ...); (b) l’invio o la lettura di un file segreto (`.env`, chiavi, `.npmrc`, `.netrc`, `.pypirc`, `.git-credentials`, credenziali AWS) come parte di un comando in uscita, e `-EncodedCommand` di PowerShell; (c) la riscrittura della cronologia del ramo predefinito su un remoto — un force push, un `+refspec`, o l’eliminazione di `main`/`master` (`git push --delete`, `git push origin :main`), anche quando lo stesso è scritto dentro `bash -c` o `powershell -Command`, e le eliminazioni con `gh api` del riferimento predefinito. Un force push su un ramo di funzionalità è sbloccato. Questi controlli sono euristiche lessicali, non un confine di sicurezza: nomi espansi da glob come `cat .e*`, stringhe composte dinamicamente come `python -c "open('.'+'env')"`, codifiche alternative, wrapper o comandi fuori dalla sintassi riconosciuta non possono essere garantiti. Una pagina malevola può ancora aggirarle. Usa la sandbox isolata per il lavoro non attendibile.
