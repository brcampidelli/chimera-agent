---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmark — dimostrare il vantaggio sui modelli deboli

La tesi di Chimera è che la struttura fa rendere un modello **debole/economico** oltre il suo
peso. Il modo onesto per dimostrarlo è un A/B controllato su un benchmark standard: fissare il
sottoinsieme di task e il modello, rendere l'**unica** variabile lo scaffolding, e riportare il
delta con un intervallo di confidenza — non un semplice "è migliorato". (Ricerche indipendenti
trovano che lo stesso modello oscilla di ~7pt solo per via dello scaffolding, quindi uno score
non qualificato non dice nulla sul *tuo* contributo.)

## L'esperimento

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — task Docker + istruzione + test di
verifica, valutati pass/fail da quei test, guidati dall'harness agnostico rispetto all'agente
**Harbor**.

- **Braccio A (baseline):** un modello gratuito nello scaffold neutro di Harbor — "modello debole
  da solo".
- **Braccio B (trattamento):** lo **stesso** modello, gli **stessi** ID di task, guidati da
  Chimera.
- **Metrica:** pass@1. **Titolo:** Δ = tasso(B) − tasso(A), con IC al 95%.
- **Guardie di onestà:** fissare il sottoinsieme di ID di task (pubblicarlo), eseguire ≥3 seed,
  pubblicare tutti i transcript, e aggiungere una riga di modello di frontiera solo come
  *riferimento di soffitto* — mai come confronto.

### Il risultato — ed è andato contro di noi

Questa pagina chiudeva la sezione nominando il numero che *avrebbe provato* la tesi: "modello
gratuito da solo = X%, modello gratuito + Chimera = Y%, Y ≫ X". L'esperimento è stato poi eseguito, e
Y è uscito **sotto** X. Su una fetta pre-registrata di N=40 con lo stesso modello in entrambi i
bracci (`deepseek-chat-v3.1`): **7,5% → 2,5%**, **Δ appaiata −5,0pp, IC 95% [−5,0%, +1,6%] — non
significativo**. L'impalcatura non ha sollevato un modello già competente; entrambi i bracci stanno
su un pavimento dominato dalla varianza. Resoconto completo, inclusa la pre-registrazione scritta
prima dell'esecuzione: [`bench/terminal_bench/RESULTS.md`](../../../bench/terminal_bench/RESULTS.md).

La frase che prometteva `Y ≫ X` è sopravvissuta all'esecuzione che l'ha smentita, su questa pagina e
in nove traduzioni. Resta registrata qui invece di essere cancellata in silenzio, perché un progetto
il cui unico vero patrimonio è la misurazione onesta non può permettersi una pagina che prevede il
contrario del proprio risultato.

## Eseguirlo

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera si collega come agente di trattamento tramite `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` costruisce un `BaseAgent` di Harbor che esegue `chimera solve`
con i flag di scaffolding). Punta Harbor verso un sottoinsieme fissato e un modello gratuito per
ogni braccio; vedi la [documentazione di Harbor](https://www.tbench.ai/) per l'invocazione esatta
di `harbor run` e `--agent-import-path`.

## SWE-bench Verified (il secondo tabellone) — **eseguito quattro volte**

Terminal-Bench dimostra la tesi su task CLI; SWE-bench la dimostra su correzioni di bug reali di
GitHub — dato un repository a un commit base e una issue, l'agente deve produrre una patch che
faccia passare i test `FAIL_TO_PASS` dell'istanza mantenendo verdi i `PASS_TO_PASS`. "Verified" è
il sottoinsieme validato da esseri umani.

### Risultati

Quattro esecuzioni pre-registrate su fette di `django/django`
(strato di difficoltà più facile), `deepseek-chat-v3.1`, pass@1, valutate **solo** dall'harness
ufficiale `swebench` 4.1.0 in Docker. Resoconto completo:
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| run | baseline | + Chimera | Δ appaiato | IC 95% | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | non significativo |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | non significativo |
| **3 (replicazione)** | 34.1% (14/41) | **43.9% (18/41)** | **+9.8%** | [−3.5%, +16.7%] | non significativo |
| **aggregato (secondario)** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **significativo** |
| 4 (attribuzione) | 34.1% | *solo impalcatura* 39.0% | +4.9% | [−7.6%, +14.2%] | non significativo |

La run 1 è uno **zero esatto** ed è pubblicata invariata. La run 2 ha corretto due difetti che
erano **nostri** — lo scaffold girava senza il suo meccanismo più forte, e 8 passi di
tool-calling non bastano per navigare un repository da 250 MB — ed è uscita con **3 istanze
vinte, 0 perse**. La coppia è il risultato: lo scaffold non vale *nulla* quando l'agente è
privato di passi, e *tre istanze* quando non lo è, e vince editando **meglio** (69% contro 57% di
precisione quando edita), non editando di più.

> ⚠️ **Nessuno di questi è uno score SWE-bench Verified.** La fetta è deliberatamente facile e a
> singolo repository, scelta perché un A/B appaiato abbia margine di misurazione; uno score
> Verified vero richiede i 500 completi. E il delta **non è significativo** — con 8 coppie in cui
> entrambi falliscono, n=19 lascia solo tre coppie informative.

La run 2 porta anche una **ritrattazione**: il meccanismo che avevamo tracciato per le patch
vuote della run 1 era sbagliato (la correzione era il budget di passi, non il diff-gate che
avevamo incolpato), corretto con lo stesso risalto con cui era stato affermato.

Quel 3–0 su tre coppie informative è esattamente la forma che produce un campione fortunato, e la
pre-registrazione gli dava **una possibilità su tre di essere solo quello**. Così l'esecuzione 3 lo
ha ripetuto su **41 istanze i cui esiti non avevamo mai visto**, senza cambiare nient'altro.
L'effetto **è riapparso**: +9,8%, dentro la banda registrata da +5 a +20, su una fetta risultata
*più difficile* di quella dell'esecuzione 2 (baseline 34,1% contro 42,1%). L'esecuzione 4 ha poi
separato l'impalcatura dal diff-gate sulle stesse 41: **+4,9% ciascuno**, e il meccanismo è la
precisione, che sale 50% → 59% → 67% mentre il tasso di patch non si muove. Nessuna esecuzione
singola è significativa; l'aggregato n=60 lo è — ed è stato pre-registrato come **secondario**
proprio perché mescola dati visti e non visti.

### L'adattatore

L'adattatore (`chimera.eval.swe_bench`) è onesto sul proprio confine: le parti pure — l'invocazione
di `chimera solve` per istanza (braccio di trattamento) e il parsing del report di valutazione
ufficiale — vivono qui e sono testate a livello unitario; il dataset e l'harness di valutazione
Docker sono **opt-in e non inclusi**, e il verdetto pass/fail arriva dai test stessi di
SWE-bench, mai autodichiarato.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Entrambi i report vengono proiettati sulla lista di istanze condivisa (un id mancante conta come
non risolto), così i due bracci sono sempre confrontati su istanze identiche — poi si applica lo
stesso verdetto Newcombe-CI.

## Valutare l'A/B (nessun benchmark necessario)

Una volta che ogni braccio ha prodotto pass/fail per task, la statistica è un unico comando —
questo non richiede **alcun extra**, quindi il motore di reporting onesto è sempre disponibile:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Ogni file è una lista JSON di booleani (o `{task_id: bool}`) sugli **stessi** ID di task. Output:
il tasso di successo di ogni braccio limitato secondo Wilson, il delta, il suo IC di Newcombe al
95%, e se la differenza è **significativa** (l'IC esclude lo zero). Se non lo è, viene riportato
senza giri di parole — un sottoinsieme più grande / più seed, oppure la funzionalità
genuinamente non sposta il numero.

Questo stesso `bench-compare` è il metro di misura per ogni funzionalità futura: ogni aggiunta di
M14 deve dimostrare che sposta il Δ sul sottoinsieme identico, oppure viene tagliata.

## La trappola onesta (cosa evitare)

- **Contaminazione** — SWE-bench pubblico ha una fuga di soluzioni documentata; preferisci
  insiemi resistenti alla contaminazione e riporta l'avvertenza.
- **Confusione di scaffold** — non riportare mai un grezzo "abbiamo segnato X%"; solo il delta
  dell'A/B isola il contributo di Chimera.
- **Baseline sbagliata / cherry-picking** — confronta debole+Chimera con lo *stesso modello
  debole da solo*, sugli ID di task *identici*, con seed e log completi. Un modello di frontiera
  è un soffitto, non un rivale.
