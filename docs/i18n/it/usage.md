---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera — Guida all'uso

Chimera è un agente CLI-first, auto-evolutivo, con un nucleo di ragionamento LLM-Fusion.
Questa guida copre installazione, configurazione, e ogni comando con esempi.

> Nuovo al progetto? Leggi prima la [panoramica sull'architettura](architecture.md).

---

## Installazione

Chimera usa [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Ogni comando qui sotto viene eseguito come `uv run chimera <command>` (o semplicemente
`chimera …` una volta che il virtualenv del progetto è nel tuo PATH).

---

## Configurazione

Chimera è agnostico rispetto al provider tramite [LiteLLM](https://docs.litellm.ai/). Metti
le tue chiavi e le scelte di modello in un `.env` locale (è ignorato da git — non
committarlo mai):

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

Altre manopole: `CHIMERA_HOME` (directory di stato, default `.chimera`),
`CHIMERA_LOG_LEVEL` (`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, default off — mette in
cache completion identiche senza tool per saltare chiamate API ripetute), e
`CHIMERA_AUTO_FUSE` (`on`/`off`, default off — fonde automaticamente i turni profondi o
**sensibili all'errore** in `solve`/`crew` senza un `--fuse` esplicito; il router
consapevole dei costi continua a mantenere in modello singolo i turni economici/con tool).
Il router riconosce prompt a risposta esatta (aritmetica, conteggio, operazioni con cifre)
nelle lingue principali del progetto (en/pt/es/de/fr/zh/ja), così un passo breve e critico
ottiene la protezione della fusione anche quando è troppo corto per far scattare il gate di
lunghezza.

**Provider, fallback & self-hosted.** Qualsiasi slug `provider/model` di LiteLLM funziona
(`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`, …). Per un server
self-hosted / compatibile con OpenAI (Ollama, vLLM) imposta `CHIMERA_API_BASE` (es.
`http://127.0.0.1:11434` con `CHIMERA_DEFAULT_MODEL=ollama/llama3`). Imposta
`CHIMERA_FALLBACK_MODELS` (separati da virgola) per passare a un altro modello se il
principale dà errore. In `chat`/`tui`, `/model <slug>` cambia il modello a metà sessione.

**Pool di credenziali.** Dai a un provider più chiavi con `CHIMERA_<PROVIDER>_KEYS` (es.
`CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). Il gateway le ruota round-robin tra le chiamate
(distribuendo carico / limiti di frequenza) e, all'interno di una singola chiamata, passa
alla chiave successiva se una dà errore. Un pool sostituisce l'unica `*_API_KEY` di quel
provider. *(I login OAuth/abbonamento — Copilot, Claude Max, ecc. — non sono ancora
collegati; le chiavi API e qualsiasi endpoint supportato da LiteLLM sì.)*

Verifica che tutto sia collegato:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Funzionalità opzionali.** Visione, la Modalità Consegnabile e il Cucciolo sono integrati.
Il resto (ricerca web, ricerca su X, generazione immagini, TTS/voce, Spotify, browser) sono
slot preconfigurati: riempi la credenziale corrispondente in `.env` (o installa la
dipendenza) e la capacità si attiva. `chimera features` è la checklist dal vivo. Il tool
`web_search` (Tavily) si auto-registra nel momento in cui `TAVILY_API_KEY` viene impostata
— ed è il modello per aggiungere gli altri (o usa il client MCP / l'importatore
OpenAPI→tool).

> **Modelli gratuiti vs a pagamento.** I modelli `:free` di OpenRouter non costano nulla ma
> hanno un limite di frequenza a monte — vanno bene per un `run` rapido, sono instabili per
> comandi a più chiamate come `fuse`/`solve`. Per un uso reale, un modello economico a
> pagamento (es. `deepseek/deepseek-chat-v3.1`, frazioni di centesimo a chiamata) è molto
> più affidabile.

---

## Comandi

### Status — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — assistente interattivo multi-turno (il tuo braccio destro)

Un REPL interattivo con memoria di conversazione e uso di tool — il pilota quotidiano.
Richiama la memoria a lungo termine rilevante e collega la conversazione tra i turni.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

Lo stesso nucleo conversazionale alimenta la TUI e il (futuro) gateway di messaggistica.

### `tui` — app da terminale a schermo intero

Una UI Textual a schermo intero sullo stesso nucleo conversazionale. Due pannelli: un
**log di conversazione** che renderizza le risposte come Markdown (il codice tra backtick è
evidenziato per sintassi), con i token del modello che **si trasmettono dal vivo** man mano
che arrivano; e un **pannello di attività** che mostra cosa ha fatto l'agente in quel turno
— i tool che ha chiamato, il conteggio dei token e il costo, e quanti fatti di memoria sono
stati richiamati. Gli stessi flag di `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Comandi: `/model <slug>` · `/reset` (pulisce il contesto) · `/clear` (pulisce lo schermo) ·
`/stream` (attiva/disattiva i token dal vivo) · `/help` · `/exit`. Tasti: `Ctrl+R` reset ·
`Ctrl+L` pulisci · `Ctrl+P` palette dei comandi · `PgUp`/`PgDn` scorri · `Ctrl+C` esci. I
comandi con slash si autocompletano mentre digiti.

Note di onestà: la trasmissione dei token esiste solo nel percorso a modello singolo — sotto
`--fuse` (un turno panel→giudice→sintetizzatore) non ci sono token incrementali, quindi il
pannello mostra uno stato "sintetizzando" invece di un cursore finto. Il costo appare come
"non disponibile" quando il prezzo di listino del modello è sconosciuto (mai indovinato).
Non c'è un indicatore verifica/ripristino qui: verifica-o-ripristina gira in
`solve`/`project`, non in chat. Se Textual non è installato, `tui` ricade sul semplice REPL
`chat`.

### `serve` — gateway di messaggistica (HTTP o Discord)

Espone l'agente con una conversazione (e la sua memoria) **per chat**. Il nucleo di routing
è agnostico rispetto al trasporto; gli adattatori si collegano ad esso.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Ogni `chat_id` mantiene il proprio contesto, così utenti/thread diversi non si mescolano.

**Operazione non presidiata (webhook).** Registra un job che scatta su un POST HTTP in
entrata, così Chimera gira senza che nessuno digiti — un push GitHub, un evento Stripe, un
ping di cron-as-a-service:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

Il corpo del POST viene passato al task del job come contesto, e ogni job registrato per
quell'hook gira. `GET /health` e `POST /chat` continuano a funzionare accanto ad esso.

**Discord nativo.** Esegui Chimera come bot Discord — ogni canale è una sessione, e
l'agente può anche inviare messaggi tramite il tool `send_message`:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Crea il bot su <https://discord.com/developers>, abilita l'intent **Message Content**, e
invitalo sul tuo server. Risponde in qualsiasi canale che riesce a vedere (filtrato per
ignorare i propri messaggi e quelli di altri bot). Il token viene letto dall'ambiente — mai
scritto direttamente nel codice.

**Telegram nativo.** Stesso pattern di adattatore, e non richiede **alcuna dipendenza
extra** (la Telegram Bot API è puro HTTP):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Slack nativo.** Riceve via Socket Mode (richiede l'extra `messaging`) e invia via la Web
API. Abilita Socket Mode sulla tua app Slack per ottenere un token a livello di app:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (invio).** WhatsApp è *basato su push* (i messaggi arrivano a un webhook Meta
che ospiti tu), quindi a differenza degli altri non c'è una connessione da aprire. Imposta
le credenziali della Cloud API e l'agente può **inviare** messaggi WhatsApp tramite il tool
`send_message` in qualsiasi modalità di `serve`:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**WhatsApp bidirezionale.** Punta il webhook della tua app Meta verso
`https://<your-host>/whatsapp` e imposta `CHIMERA_WHATSAPP_VERIFY_TOKEN` (qualsiasi stringa
tu scelga, corrispondente alla config dell'app). `chimera serve` verifica quindi
l'iscrizione (`GET /whatsapp`) e instrada i messaggi in entrata (`POST /whatsapp`)
attraverso il gateway, rispondendo tramite la Cloud API. WhatsApp richiede comunque un URL
pubblico per il webhook — quella è l'unica parte fuori da Chimera.

**Signal nativo (bidirezionale).** Signal non ha un'API ufficiale, quindi Chimera parla con
un bridge [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api) che
esegui tu (Docker) e colleghi al tuo numero — puro HTTP, nessuna dipendenza Python:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, completion in un colpo solo

Una singola chiamata al modello, senza tool, senza fusione. Il percorso più economico.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Visione / incollare immagine.** Allega immagini con `--image` (un percorso o URL,
ripetibile) — richiede un modello capace di visione:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Modalità Consegnabile (produce un artefatto)

Dove `run`/`chat` rispondono in modo conversazionale, `deliver` produce un documento
completo e autonomo (report, piano, spec, README...) e lo scrive su un file.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — il loop grezzo di tool-calling ReAct

Pensiero → Azione (tool) → Osservazione, fino a una risposta finale. I tool sono limitati
al workspace.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (il differenziatore)

Esegue un *panel* di modelli, un *giudice* analizza le loro risposte
(consenso / contraddizioni / punti ciechi), e un *sintetizzatore* scrive la risposta
finale. Usa `--show-panel` per vedere la traccia completa.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

La fusione costa ~2-3× una chiamata singola, quindi riservala per il ragionamento
difficile. `fuse` stampa anche il costo in token per fase (panel / giudice / sintesi) così
puoi vedere dove vanno davvero i token di un'esecuzione.

**Fusione selettiva (ATTIVA per default, risparmia token).** Il motore sonda i primi
`CHIMERA_FUSION_PROBE_K` modelli del panel (default 2) e, quando le loro risposte
concordano da vicino, salta il resto del panel *e* il giudice — sintetizzando
direttamente dalle risposte concordanti. Il controllo di accordo è un confronto di testo
locale economico (nessuna chiamata a modello extra), quindi un turno *discordante* fa
escalation alla pipeline completa e costa esattamente come la fusione completa, mentre un
turno *concordante* è più economico. Regola la soglia con `CHIMERA_FUSION_AGREEMENT` (0–1,
default 0.8), oppure imposta `CHIMERA_FUSION_MODE=full` (o passa `--full`) per eseguire
sempre l'intero panel + giudice.

Perché è il default: su 3 esecuzioni di `chimera fusion-bench --tasks hard` (un panel a
pagamento di 3 modelli) ha tagliato i token del **~20–28%** ed è stato corretto in
**ogni** turno in cui ha effettivamente interrotto in anticipo (16/16). L'accuratezza
complessiva ha oscillato da 0 a −8,3pp tra le esecuzioni, ma quella varianza cade
interamente nel bucket *escalato* — dove la modalità selettiva esegue la pipeline identica
a quella completa — quindi è non-determinismo del modello, non un costo dell'interruzione
anticipata. Esegui il bench sul tuo carico di lavoro per vedere il trade-off per il tuo
panel e i tuoi task:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Scegli modelli di panel affidabili.** La fusione paga solo se ogni membro del panel
> risponde davvero. Evita gli slug di modello `:free` di OpenRouter in
> `CHIMERA_FUSION_PANEL` — hanno limiti di frequenza (HTTP 429) sotto carico reale, e il
> panel si restringe silenziosamente a qualunque modello a pagamento rimanga. Un trio
> economico e affidabile: `openrouter/deepseek/deepseek-chat`,
> `openrouter/openai/gpt-4o-mini`, `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Skill card (carte di ragionamento TRS, sperimentale)

L'agente distilla ciò che apprende in **carte di ragionamento** — i cinque campi
Trigger / Do / Avoid / Check / Risk (più parole chiave di recupero) — sia dai successi
(una carta di *pattern*) sia dai fallimenti ricorrenti (una carta consultiva di
*anti-pattern*). Quando `CHIMERA_SKILL_CARDS=on`, `solve` recupera le top-k carte
rilevanti (BM25 su nome + descrizione + trigger) e le inietta nel contesto di ragionamento
del worker, così l'agente riusa ciò che ha funzionato ed evita modalità di fallimento
note. Questo chiude il ciclo — prima, le skill apprese venivano memorizzate e mai rilette.

Disattivato per default: iniettare carte aggiunge token di prompt, e i risparmi di *token*
del TRS derivano dall'accorciare tracce di ragionamento lunghe, quindi su task a risposta
breve il guadagno è in accuratezza, non in costo. Questo non è ipotetico — sulla suite a
risposta breve `hard` (deepseek-v3.1 a pagamento), `skillcard-bench` ha misurato carte che
costano **+290% di token** e **−8pp di accuratezza** rispetto a nessuna carta: con un
modello vicino al soffitto e senza una traccia lunga da accorciare, le carte generiche sono
puro overhead che può distrarre. Abilita le carte per carichi di lavoro a
**ragionamento lungo** (matematica/codice con tracce estese) dove la matematica dei token
si inverte, e misura sempre prima il tuo trade-off con un controllo di ground-truth:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

Il bench riporta l'accuratezza con vs senza carte, il delta di token, il tasso di hit
delle carte, e l'accuratezza suddivisa per hit/miss, con un verdetto PASS quando
l'accuratezza con carte resta entro 1pp dalla baseline senza carte.

### Schema di tool compatti (sperimentale)

Gli schema dei tool — specialmente quelli importati da server MCP o spec OpenAPI —
portano rumore di annotazione (esempi, titoli, default, prosa di parametro multi-frase,
corpi di richiesta annidati) che viene reinviato al modello a **ogni** passo ReAct. Con
`CHIMERA_COMPACT_SCHEMAS=on`, quel rumore viene rimosso e le descrizioni dei parametri
tagliate al momento della pubblicizzazione, **senza** toccare nulla che influisca su una
chiamata (il nome e la descrizione della funzione, e ogni `type` / `properties` /
`required` / `enum` dello schema sono preservati). Gli schema canonici restano intatti —
solo la copia inviata al modello si riduce.

Il risparmio è maggiore su toolset MCP/OpenAPI verbosi e si accumula a ogni passo; i tool
nativi sono già concisi, quindi la loro riduzione è piccola. Misura prima il tuo toolset
(nessuna chiamata al modello — conta solo i token):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Disattivato per default. Poiché la compattazione rimuove solo rumore di annotazione (mai
la struttura), l'unico rischio è che il modello abbia leggermente meno prosa per scegliere
un tool — quindi resta conservativa, e dovresti confermare il comportamento di
tool-calling sul tuo carico di lavoro prima di abilitarla.

### `solve` — Tier-2 autonomo (piano + verifica-o-ripristina)

Pianifica il task, esegue con il loop dell'agente, poi **verifica con un comando
eseguibile**. Se la verifica fallisce, ripristina il workspace e riprova con feedback. Il
verificatore (exit code 0 = successo) è la verità di riferimento.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Flag utili:

| Flag | Significato |
|------|---------|
| `--verify "<cmd>"` | comando che deve uscire con 0 (test, una build, un linter) |
| `--workspace`, `-w` | dove l'agente legge/scrive (default `.`) |
| `--max-attempts N` | budget di verifica-o-ripristina (default 3) |
| `--max-steps N` | passi di tool-calling per tentativo (default 8) |
| `--fuse` | produce il **piano** via fusione (ragionamento profondo) |
| `--guard` | regola ogni chiamata di tool attraverso il kernel di governance |
| `--no-plan` / `--no-manager` | salta la fase di pianificazione / review |
| `--rubric` | il Manager giudica tramite la **rubrica a cascata** (seguire l'istruzione → fattualità → razionalità) |
| `--no-remember` | non scrive automaticamente un fatto di memoria al successo |
| `--no-evolve-skills` | non propone automaticamente una skill appresa quando un task si ripete |
| `--isolate` | gira in un git worktree usa-e-getta; i file modificati vengono copiati indietro solo al successo |
| `--require-diff` | un tentativo che non ha modificato **nessun file** fallisce e viene riprovato — per un task di codice, una spiegazione non è una correzione |
| `--keep-workspace` | al fallimento, lascia su disco le modifiche dell'ultimo tentativo invece di ripristinare — per quando un valutatore **esterno** decide passa/fallisce |
| `--diff-feedback` | mostra a un tentativo fallito il proprio diff ripristinato, inquadrato come un percorso da non ripercorrere |
| `--stagnation-fuzzy` | fa corrispondere le firme di fallimento ripetuto in modo approssimativo, così il pivot anti-stallo scatta su fallimenti della stessa causa la cui formulazione differisce |

> **Su `--max-steps`.** Il default di 8 è calibrato per workspace piccoli. Su un
> **repository grande è il vincolo determinante**, non il modello: la run 1 di SWE-bench ha
> segnato uno 0,0pp esatto con 8 passi contro un checkout di 250 MB, e la stessa
> configurazione a **30 passi** ha alzato il tasso di patch della baseline dal 47% al 74%
> ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)). Se l'agente esplora e
> poi finisce senza modificare nulla, alza prima questo.

> **`--require-diff` e `--keep-workspace` sono per la valutazione esterna.** `solve` è
> verifica-o-ripristina: quando è *lui* a possedere la decisione passa/fallisce,
> ripristinare un tentativo fallito è corretto. Quando è qualcos'altro a possederla — un
> job CI, un harness di benchmark, un umano che rivede il diff — `--keep-workspace` impedisce
> che il lavoro dell'agente venga annullato prima che quel giudice lo veda mai, e
> `--require-diff` impedisce che una spiegazione sicura di sé venga valutata come una
> modifica completata. Entrambi sono **disattivati per default**.

**`solve` impara tra le esecuzioni.** Ogni esecuzione alimenta un ciclo comportamentale
chiuso, tutto regolato da verifica-o-ripristina così che solo il lavoro verificato abbia
effetto: (1) le **lezioni** rilevanti dai tentativi passati (i fallimenti sono favoriti)
vengono incorporate nel piano/prompt, e il **primo passo difettoso** di un tentativo
fallito viene localizzato e passato al nuovo tentativo; (2) a un successo verificato viene
scritto un fatto di **memoria** deduplicato (richiamato più tardi da `chat`/`crew`); e (3)
quando un pattern di task si ripete (≥ 2 successi precedenti), viene proposta una **skill**
riutilizzabile — attraverso il panel di fusione e mantenuta per **trasferibilità**
cross-model quando `--fuse` è attivo — e viene mantenuta solo se supera la validazione di
governance e uno smoke test eseguibile.

### `crew` — multi-agente Tier-3

Un team di agenti con ruoli collabora su un task e un supervisor sintetizza la risposta
finale.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — crew SDLC (pianifica → costruisci → testa → rivedi)

Una pipeline di ciclo di vita del software pre-assemblata con **verifica-o-ripristina**
nella fase di test: `plan` decompone il task, `build` lo implementa, `test` esegue il
verificatore (ripristinando e riprovando la build al fallimento), e un revisore critica il
risultato.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Ogni fase stampa con un ✓/✗; l'esecuzione è `success` solo se il verificatore della fase
di test è passato.

### `meta` — agenti che costruiscono agenti

Progetta il blueprint di un agente specializzato (nome, tool, prompt di ruolo) per un task.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — verdetto di governance

Mostra la decisione del kernel di fiducia (allow / warn / review / block) per un'azione.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — benchmark di evoluzione continua

Misura se le prestazioni *reggono* lungo una catena di task (la prova anti-degradazione):
tasso di successo complessivo, prima metà vs seconda metà, striscia più lunga.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

Il report porta anche una flag di degradazione **statisticamente onesta**: invece di
fidarsi di una semplice sottrazione prima-meno-seconda-metà (su una catena corta
un'oscillazione di 0,2 è di solito rumore), `degraded_significant` è `1.0` solo quando un
intervallo di confidenza di Wilson sul calo esclude lo zero, `-1.0` quando il campione è
troppo piccolo per dirlo, e `0.0` altrimenti — più i limiti `degradation_ci_low/high`.
Separatamente, `CHIMERA_SKILL_ACCEPT_MODE=wilson` condiziona la decisione di accettazione
skill cross-model al limite di confidenza *inferiore* del tasso di trasferimento (così un
2-su-3 fortunato non conta più); il default `point` mantiene il tasso grezzo, dato che il
limite di Wilson è rigoroso su panel minuscoli.

### `sandbox-bench` — valutazione di stato + effetto collaterale

I bench testuali valutano la *risposta* del modello; questo valuta ciò che l'agente **ha
fatto**. Ogni task gira in una directory sandbox isolata, e l'harness confronta lo stato
finale dei file con l'obiettivo (qualsiasi percorso consentito, stile risultato) **e**
separatamente conta gli *effetti collaterali dannosi* — mutazioni fuori dall'insieme
consentito dichiarato per il task. Così un agente che produce il risultato giusto mentre
distrugge un file non correlato viene beccato, non valutato come un successo pulito.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Riporta `pass_rate` e `side_effect_rate`. Include la *metodologia* (uno `StatefulTask` con
`goal_check` + insieme `allowed` di mutazione), non una grande suite di task — scrivi task
per i tuoi stessi tool. I valutatori testuali esistenti restano corretti per lavoro
puramente di domanda-risposta.

### `memory` — memoria a lungo termine curata

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

Il richiamo passa attraverso un **gate di ammissione** (un confine di fiducia): una memoria
richiamata entra nel prompt solo se è rilevante *e* priva di testo di override/injection
(difesa da jailbreak basato sulla memoria). `memory prune` dimentica sotto un budget
tramite un modello di **valore** multifattoriale (recenza, specificità, tipo, curatela,
affidabilità) — non un singolo criterio.

Il **livello a grafo** estrae triple `(fonte, relazione, target)` dalle tue memorie
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), così i fatti possono essere
richiamati per entità, non solo per parola chiave.

### `cron` — job pianificati & SOP di evento

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — bacheca dei task con corsie per worker

Una bacheca (`backlog → doing → review → done`) dove ogni card nomina una *corsia* che la
dispaccia allo stack dell'agente: `solve` (autonomo Tier-2, verifica-o-ripristina) o
`crew` (pipeline di ruoli Tier-3). La vista operativa del loop che l'agente già esegue.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` percorre ogni card backlog → doing → done (successo) oppure → review (richiede
attenzione). `learn` riusa il rilevatore di ricorrenza del cron-learner per accodare i
task che l'agente ripete (deduplicati contro la bacheca) — pianificalo per riempire
automaticamente il backlog.

### `workflow` — loop progettati (Loop Engineering)

Scrivi un loop autonomo come YAML invece di un prompt improvvisato. Ogni passo `uses` una
capacità (`run` / `shell` / `solve` / `crew` / `lifecycle`), può essere condizionato al
passo precedente (`when: prev_succeeded | prev_failed`), e può ripetersi (`repeat`, `until:
success`).

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` — gate di drift spec↔codice

Mantiene allineati una spec e il codice. Una spec è un piccolo YAML di requisiti (`defines`
un simbolo / `contains` una regex / `absent` una regex / `command` esce con 0). Il gate
esce con codice diverso da zero in caso di drift, quindi funge anche da verificatore.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — importare da un altro agente

Porta **config + skill** da Hermes o OpenClaw, e con `--apply` fa anche il **merge della
memoria a lungo termine** (deduplicata, non distruttiva). Il default è un'anteprima
dry-run.

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

Il merge della memoria riporta i conteggi `{ADD, UPDATE, NOOP}` — i duplicati diventano
`NOOP`, quindi rieseguire è sicuro.

### `evolve` — evoluzione del modello opt-in (avanzato)

`chimera solve --collect` (attivo per default) registra ogni esecuzione come traiettoria.
I comandi `evolve` la trasformano in dataset pronti per il training e una recipe LoRA
eseguibile. **Il training è esterno e opt-in** — cambia i pesi del modello, quindi non
avviene mai automaticamente; Chimera prepara i dati e uno script e si ferma.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` accetta manopole di recipe: `--min-steps N` mantiene solo tracce a lungo
orizzonte, `--diverse` mantiene al massimo un esempio per task (la diversità dei task è il
collo di bottiglia della curatela), e `--min-process P` (SkillCoach) mantiene solo tracce
il cui punteggio di *aderenza al processo* ≥ P — la frazione di passi di tool che ha
prodotto un risultato riuscito e visibile — così un successo fortunato che si è dibattuto
tra chiamate di tool fallite non entra nel training. Gli eventi per singolo passo dietro
quel punteggio sono catturati automaticamente a ogni esecuzione di `solve`; il filtro è
disattivato per default (`CHIMERA_SFT_MIN_PROCESS` imposta un default globale).
`evolve tune` è diverso dal training — esegue una **meta-ricerca** sulla *spec*
dell'agente (modello, prompt di sistema, budget di passi, panel, profondità di memoria),
valutando ogni candidato sugli scenari giornalieri e mantenendo una modifica solo in caso
di **non-regressione**. Chiama modelli ma non cambia mai i pesi, quindi è sicuro da
eseguire in qualsiasi momento.

Poi, per addestrare davvero, su una GPU (o Colab): `pip install chimera-agent[train]` (o
il `requirements.txt` della recipe) e `python recipe/train.py`. Punta
`CHIMERA_DEFAULT_MODEL` verso il modello base + adapter quando servi.

### `pet` — un compagno virtuale

Un piccolo compagno persistente le cui statistiche cambiano mentre sei via. Non serve
alcuna chiave.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Consigli

- **Tool vs ragionamento.** I turni di tool-calling usano sempre un singolo modello (la
  fusione non può chiamare tool); la fusione è riservata al ragionamento profondo senza
  tool.
- **Ispeziona cosa è successo.** `CHIMERA_LOG_LEVEL=DEBUG` mostra i log di routing e di
  attivazione della fusione.
- **Mantieni i test onesti.** Un buon comando `--verify` (una vera suite di test) rende
  `solve` affidabile — è la verità di riferimento eseguibile a cui l'agente è sottoposto.
