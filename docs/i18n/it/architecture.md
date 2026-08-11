---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Architettura

Questo documento mappa il codebase sul design e sulla ricerca su cui si basa. Per il "perché",
vedi [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## Il nucleo di ragionamento: LLM-Fusion

`chimera/fusion/`

Il motore di fusione esegue un task attraverso un **panel** di modelli, fa produrre a un
**giudice** un'analisi strutturata (consenso / contraddizioni / copertura parziale / intuizioni
uniche / punti ciechi), poi un **sintetizzatore** scrive la risposta finale fondata su
quell'analisi (`FusionEngine`). Implementa il protocollo `SupportsComplete`, quindi è un backend
di ragionamento plug-and-play ovunque ci si aspetti un modello — incluso dentro il loop
dell'agente.

Un **router consapevole dei costi** (`RoutedBackend` + `RoutingPolicy`) mantiene la fusione
selettiva: i turni di tool-calling vanno a un singolo modello (la fusione non fa tool-calling), e
solo i turni di ragionamento profondo / ad alto rischio vengono fusi. Ispirato a OpenRouter
Fusion (il guadagno viene dal passo di *sintesi*, non solo dalla diversità dei modelli) e ad
AURORA-AI (budget adattivo tra modelli eterogenei).

## Il loop dell'agente & l'autonomia Tier-2

`chimera/core/`

- `Agent` — un loop minimale di ReAct / tool-calling con un **transcript esplicito** (lo stato
  vive fuori dal modello). Dipende solo da `SupportsComplete` + un `ToolRegistry`.
- `AutonomousAgent` — Tier-2: assembla il contesto **Spine** con ambito di proprietà →
  **pianifica** → snapshot → esegue → **revisione del Manager** (genera-vs-verifica) →
  **verifica-o-ripristina** → riprova con feedback, registrando ogni tentativo nel buffer di
  esperienza.
- `WorkspaceGuard` — snapshot/ripristino di file di testo, il meccanismo dietro il
  verifica-o-ripristina.
- `CommandVerifier` — "prova eseguibile" (exit 0 == successo).

### Attaccare la degradazione dell'evoluzione continua

Il problema aperto (secondo *Agentic Software*, `2606.05608`): le prestazioni cadono da >80% su
task isolati a ~38% in evoluzione continua — contesto a lungo orizzonte + propagazione
dell'errore. Le contromisure di Chimera, ciascuna fondata sulla letteratura:

| Contromisura | Dove | Base |
|---|---|---|
| Esternalizzare lo stato (transcript/workspace, non il contesto dell'LLM) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Contesto con ambito di proprietà (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Supervisione genera-vs-verifica | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verifica-o-ripristina | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Buffer di esperienza (fallimenti come negativi) | `evolution/experience.py` | HORIZON `2606.28279` |
| Consolidamento messaggi nei team | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark di evoluzione continua | `eval/continuous.py` | Dichiarazione del problema EvoClaw |

## Memoria & auto-evoluzione

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — elementi gerarchici (working / episodic / semantic / persona) con
  `ADD / UPDATE / DELETE / NOOP` (`remember`) e deduplica tramite `merge` (Memory-R1, `2606.14502`).
- **Skill evolver** — `SkillEvolver` propone una `LearnedSkill` riutilizzabile a partire da un
  successo, la testa e la mantiene solo se passa (proponi → testa → mantieni/scarta). Le skill
  apprese sono **template di prompt, non codice eseguibile** — sicuro da produrre in autonomia
  prima di qualsiasi auto-modifica a livello di codice. Il raffinamento migliora un template a
  partire dai suoi fallimenti (VIBEMed `2606.15504`).
- **Cron auto-appresi** — `CronLearner` rileva task ricorrenti e propone cron
  (`created_by=agent`, **disabilitati** in attesa di approvazione umana).
- **Benchmark di evoluzione continua** — esegue una catena di task attraverso un solver e riporta
  la degradazione (tasso di successo complessivo, prima metà vs seconda metà, striscia più lunga).

## Governance & sicurezza

`chimera/governance/`

Un kernel di fiducia auto-migliorante (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Il `RuleSet` lessicale
  gestisce deterministicamente le minacce a firma fissa; un **giudice semantico** opzionale
  gestisce l'intento; regole distillate lo rendono più economico nel tempo. Invariante: **mai
  bloccare in modo rigido un'azione benigna**.
- `SkillValidator` / `ScheduleValidator` — la **superficie di modifica vincolata e
  verificabile staticamente** per l'auto-modifica (AutoMegaKernel `2606.09682`): le proposte
  non sicure sono respinte prima ancora di girare.
- `AuditLog` — JSONL append-only di decisioni e cambiamenti evolutivi.
- `GovernedTool` / `govern_registry` — avvolge qualsiasi tool così che la sua esecuzione sia
  regolata; si compone con il loop dell'agente esistente senza alterarlo (`chimera ... --guard`).

### Il livello di taint (contenimento del prompt-injection)

Sovrapposto al kernel — euristico, onesto, e mai un confine rigido (la sandbox lo è):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — un ledger di capability per
  esecuzione. Un fetch contamina il suo contenuto; una scrittura/esecuzione che consuma contenuto
  contaminato **fa escalation a review** (`assess_action`). Il contenuto recuperato non fidato
  viene restituito **recintato come dato** e con i token di controllo del chat-template rimossi
  (`sanitize.py`), e gli artefatti durevoli da un'esecuzione contaminata mantengono una
  provenienza `tainted` così che il veleno non possa ripulirsi in una memoria/skill "pulita".
- `AggregateMonitor` (`aggregate_monitor.py`) — un monitor un livello più in alto: dati gli
  eventi di capability di ogni sotto-agente, cattura **flussi divisi** che un monitor per singolo
  agente non può vedere (l'agente A recupera contenuto non fidato, l'agente B lo esegue o lo
  **esfiltra**).
- `check_drift` (`drift.py`) — una `Spec` di requisiti eseguibili (`defines`/`contains`/`absent`/
  `command`) che funge sia da verità di riferimento per `solve --verify` sia da autorità
  dell'orchestratore di progetto su cosa significhi "fatto" (sotto). I controlli negativi
  falliscono in modo chiuso sui file che non riescono a scansionare.
- `QuarantineTool` + allowlist adattiva (`quarantine.py`, `allowlist.py`) — un reader in
  quarantena dual-LLM/CaMeL e un'allowlist di tool adattiva al taint che si restringe non appena
  un'esecuzione è contaminata.

## Team multi-agente (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — specializzazione di ruolo (stile CrewAI).
- `SequentialCrew` — ruoli in ordine, ognuno vede gli output precedenti **consolidati** e può
  scrivere nella memoria condivisa.
- `SupervisorCrew` — i worker affrontano il task in parallelo, gli output vengono consolidati, e
  un supervisor sintetizza (stile CAPRA `parallel_review`, `2606.18976`).
- `consolidate` — la fusione dei messaggi MOC mantiene snello il contesto del team (`2606.02359`).

## Ecosistema auto-evolutivo (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — progetta/costruisce/valuta agenti specializzati (agenti che costruiscono
  agenti). Due salvaguardie dal Meta-Agent Challenge (`2606.04455`): **isolamento dei tool** (i
  tool di un agente progettato sono filtrati a una lista consentita) e **separazione dei test
  nascosti** (passare il visibile + fallire il nascosto ⇒ sospetto reward-hacking, non
  accreditato come successo).
- `ChangeQueue` — governa il *ritmo* dei cambiamenti (coda di merge FIFO + limiti di lotto), non
  l'organico ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — registra (prompt, risposta, esito) ed esporta dataset **SFT / DPO**. Il
  fine-tuning vero e proprio è **opt-in ed esterno** — Chimera raccoglie, non addestra.

## Economia dei costi & la gerarchia di delega

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

La delega paga solo quando è più economica del fare il lavoro inline, e l'affermazione è
**misurata, non asserita**:

- `HierarchicalOrchestrator` — decompone → dispaccia worker con budget → verifica ogni
  risultato → sintetizza. Il fan-out a forma di lettura delega; una sottotask banalmente piccola
  viene risposta inline dal modello di fiducia al vertice.
- `CascadeBackend` — debole → gate → medio → gate → fusione, salendo di livello solo quando la
  risposta di un livello fallisce un gate di accettazione economico. Il **route log** registra
  ogni salto, quindi il costo è la **somma su tutti i salti tentati**, non solo quello accettato
  — le escalation vengono pagate.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — un tetto rigido di token applicato al
  backend, per worker.
- `EnvelopeVerifier` — schema → criteri di accettazione → **spot check** probabilistico (valuta
  la fedeltà di un riassunto contro l'artefatto grezzo); una nuova richiesta scatenata da un
  fallimento dello spot check viene ri-auditata.
- **Ricevute di delega** (`receipts.py`) — ogni delega registra i suoi token/costo misurati **e
  il controfattuale inline nella stessa riga**, prezzato alla tariffa propria di ogni modello
  (modello sconosciuto → `None`, mai inventato). Anche l'overhead di decomposizione/sintesi
  dell'orchestratore stesso viene misurato, così `summarize_delegations`
  (`chimera delegations`) riporta un risparmio netto **auditabile**, e `cascade-bench` riporta la
  **coda** dei costi (p50/p95/p99), non solo la media.

## Il flywheel dell'auto-evoluzione

`chimera/evolution/`

Il "training" che non tocca mai i pesi — segnalato dalla fitness, senza gradiente, e reversibile:

- `EvolutionContext` — l'assemblaggio condiviso (experience, trajectories, memory,
  auto-evolver, skill card, playbook) che rende l'apprendimento una proprietà dello *stack*
  dell'agente, non solo del comando `solve`.
- Skill card + raffinamento **GEPA**, il **playbook** ACE, e una `SkillLifecyclePolicy` che
  promuove/retrocede una skill in base alle sue statistiche **misurate** di uso/successo (una
  nuova skill nasce `provisional`).
- Il **diff-gate** — un "successo vuoto" (il verificatore è passato ma il diff del workspace è
  vuoto) non genera una skill o una memoria; il flywheel impara solo da lavoro che è
  effettivamente avvenuto.
- Il **transfer-gate** (`eval/transfer.py`) — un artefatto ottimizzato viene promosso solo se
  regge anche su un holdout, proteggendo contro il trasferimento negativo.
  `maturity.Scorecard.weakest()` è l'obiettivo: il loop punta alla capacità più debole. Le
  regressioni fanno rollback automatico solo di fronte a un calo **statisticamente
  significativo** (un IC, mai un singolo punto).

Ogni cambio di un default passa attraverso un A/B appaiato **pre-registrato** (`bench/`),
pubblicato che vinca o che perda — senza ripetere finché non è significativo.

## Autonomia di progetto (dall'inizio alla fine)

`chimera/orchestration/project.py`

`ProjectOrchestrator` esegue un intero progetto contro una `Spec`: grafo di task (un DAG in
stile Kanban con `depends_on`) → ogni card pronta viene risolta (con il contesto di evoluzione
sopra) → **accettata contro la Spec** tramite `check_drift` (l'unica autorità su cosa significhi
"fatto") → i requisiti non soddisfatti generano le card successive, ripetendo finché la Spec è
allineata o un budget / un massimo di iterazioni / un checkpoint umano lo ferma. I passi
rischiosi (`risk: high` — deploy / migrazione / cancellazione) **si mettono in pausa per
l'approvazione umana**; l'esecuzione è durevole e ripristinabile.

## Trasversale

- **Providers** (`providers/`) — un unico gateway agnostico rispetto al provider su LiteLLM; le
  chiavi possono vivere in `.env` e vengono esportate nell'ambiente perché LiteLLM le veda.
- **Tools** (`tools/`) — primitive native; i metadati dei tool sono attributi di istanza così
  che i tool generati dinamicamente (OpenAPI/MCP) funzionino.
- **Integrations** (`integrations/`) — client MCP (extra opzionale `mcp`) + importatore
  OpenAPI→tool + registro di connettori.
- **Scheduler** (`scheduler/`) — cron + SOP di evento; il tempo è iniettato per test
  deterministici.
- **Migration** (`migration/`) — importa config + skill + fa il **merge** della memoria a lungo
  termine da Hermes / OpenClaw, deduplicata e non distruttiva.

## Filosofia dei test

Ogni sottosistema è testato a livello unitario con **backend fittizi** — deterministici, senza
rete, senza chiavi. I comandi che chiamano davvero un LLM hanno uno smoke test per il loro
percorso di fallimento senza chiave. Il quality gate (`ruff` + `mypy --strict` + `pytest`) gira
in CI su Python 3.11 e 3.12.
