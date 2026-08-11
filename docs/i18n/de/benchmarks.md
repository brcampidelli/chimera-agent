---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmarks — den Lift für schwache Modelle beweisen

Chimeras These ist, dass Struktur ein **schwaches/günstiges** Modell über sein Gewicht hinaus
schlagen lässt. Der ehrliche Weg, das zu zeigen, ist ein kontrolliertes A/B auf einem
Standard-Benchmark: die Aufgabenteilmenge und das Modell fixieren, das **einzige** Variable das
Scaffolding sein lassen und das Delta mit einem Konfidenzintervall melden — nicht ein bloßes
"es ist besser geworden". (Unabhängige Forschung findet beim selben Modell Schwankungen von ~7
Punkten allein durch das Scaffolding, sodass ein unqualifizierter Score nichts über *deinen*
Beitrag aussagt.)

## Das Experiment

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — Docker-Aufgabe + Anweisung +
Verifikationstests, bewertet pass/fail durch diese Tests, gesteuert vom agentenagnostischen
**Harbor**-Harness.

- **Arm A (Baseline):** ein freies Modell in Harbors neutralem Scaffold — "schwaches Modell
  allein".
- **Arm B (Behandlung):** **dasselbe** Modell, **dieselben** Task-IDs, gesteuert von Chimera.
- **Metrik:** pass@1. **Kernzahl:** Δ = rate(B) − rate(A), mit einem 95%-KI.
- **Ehrlichkeits-Schutzmaßnahmen:** die Task-ID-Teilmenge festpinnen (veröffentlichen), ≥3 Seeds
  laufen lassen, alle Transkripte veröffentlichen und eine Frontier-Modell-Zeile nur als
  *Obergrenzen-Referenz* hinzufügen — nie als Vergleich.

Die eine Zahl, die die These beweist: **freies Modell allein = X %, freies Modell + Chimera = Y %,
dieselben Aufgaben, Y ≫ X.**

## Ausführen

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera klinkt sich als Behandlungs-Agent über `chimera/eval/terminal_bench.py` ein
(`make_chimera_tb_agent(model)` baut einen Harbor-`BaseAgent`, der `chimera solve` mit den
Scaffolding-Flags ausführt). Harbor auf eine festgepinnte Teilmenge und ein freies Modell für
jeden Arm richten; den genauen `harbor run`-Aufruf und `--agent-import-path` siehe die
[Harbor-Dokumentation](https://www.tbench.ai/).

## SWE-bench Verified (das zweite Scoreboard) — **zweimal ausgeführt**

Terminal-Bench beweist die These bei CLI-Aufgaben; SWE-bench beweist sie bei echten
GitHub-Bugfixes — gegeben ein Repo bei einem Basis-Commit und ein Issue, muss der Agent einen
Patch erzeugen, der die `FAIL_TO_PASS`-Tests der Instanz bestehen lässt, während `PASS_TO_PASS`
grün bleibt. "Verified" ist die menschlich validierte Teilmenge.

### Ergebnisse

Zwei vorregistrierte Läufe auf demselben eingefrorenen 19-Instanzen-`django/django`-Slice
(leichteste Schwierigkeitsstufe), `deepseek-chat-v3.1`, pass@1, bewertet **ausschließlich** vom
offiziellen `swebench`-4.1.0-Harness in Docker. Vollständiger Bericht:
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| Lauf | Baseline | + Chimera | gepaartes Δ | 95%-KI | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36,8 % (7/19) | 36,8 % (7/19) | +0,0 % | [−8,5 %, +8,5 %] | nicht signifikant |
| 2 (`max_steps=30`) | 42,1 % (8/19) | **57,9 % (11/19)** | **+15,8 %** | [−1,9 %, +15,8 %] | nicht signifikant |

Lauf 1 ist eine **exakte Null** und wird unverändert veröffentlicht. Lauf 2 behob zwei Fehler, die
*unsere eigenen* waren — das Scaffold lief ohne seinen stärksten Mechanismus, und 8
Tool-Calling-Schritte reichen nicht aus, um sich in einem 250-MB-Repository zurechtzufinden — und
kam auf **3 gewonnene Instanzen, 0 verloren**. Das Paar ist der Befund: Das Scaffold ist *nichts*
wert, wenn der Agent an Schritten hungert, und *drei Instanzen* wert, wenn nicht, und es gewinnt
durch **besseres** Editieren (69 % vs. 57 % Präzision, wenn editiert wird), nicht durch mehr
Editieren.

> ⚠️ **57,9 % ist kein SWE-bench-Verified-Score.** Der Slice ist absichtlich leicht und
> Single-Repo, gewählt, damit ein gepaartes A/B Spielraum zum Messen hat; ein echter
> Verified-Score braucht die vollen 500. Und das Delta ist **nicht signifikant** — bei 8 Paaren,
> in denen beide scheitern, bleiben bei n=19 nur drei informative Paare übrig.

Lauf 2 bringt auch eine **Widerrufung** mit: Der Mechanismus, den wir für Laufs 1 leere Patches
verantwortlich gemacht hatten, war falsch (der Fix war das Schrittbudget, nicht das Diff-Gate,
dem wir die Schuld gegeben hatten), korrigiert ebenso prominent, wie es behauptet wurde.

### Der Adapter

Der Adapter (`chimera.eval.swe_bench`) ist ehrlich über seine Grenze: Die reinen Teile — der
Aufruf von `chimera solve` pro Instanz (Behandlungsarm) und das Parsen des offiziellen
Evaluationsberichts — liegen hier und sind unit-getestet; der Datensatz und der
Docker-Evaluationsharness sind **opt-in und nicht mitgeliefert**, und das Pass/Fail-Urteil kommt
aus SWE-benchs eigenen Tests, nie selbst gemeldet.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Beide Berichte werden auf die gemeinsame Instanzliste projiziert (eine fehlende ID zählt als
ungelöst), sodass die beiden Arme immer auf identischen Instanzen verglichen werden — dann greift
dasselbe Newcombe-KI-Urteil.

## Das A/B bewerten (kein Benchmark nötig)

Sobald jeder Arm Pass/Fail pro Aufgabe geliefert hat, ist die Statistik ein einziger Befehl —
dafür ist **nichts Zusätzliches** nötig, die ehrliche Auswertungs-Engine ist also immer verfügbar:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Jede Datei ist eine JSON-Liste von Booleans (oder `{task_id: bool}`) über **dieselben** Task-IDs.
Ausgabe: die Wilson-begrenzte Pass-Rate jedes Arms, das Delta, sein Newcombe-95%-KI und ob der
Unterschied **signifikant** ist (das KI schließt null aus). Ist er es nicht, wird das offen
gemeldet — eine größere Teilmenge / mehr Seeds, oder das Feature bewegt die Zahl schlicht nicht.

Dasselbe `bench-compare` ist der Maßstab für jedes spätere Feature: Jede M14-Ergänzung muss
zeigen, dass sie Δ auf der identischen Teilmenge bewegt, sonst wird sie gestrichen.

## Die ehrliche Falle (was zu vermeiden ist)

- **Kontamination** — öffentliches SWE-bench hat dokumentierte Lösungslecks; kontaminationsresistente
  Sets bevorzugen und den Vorbehalt melden.
- **Scaffold-Konfundierung** — nie ein rohes "wir haben X % erreicht" melden; nur das A/B-Delta
  isoliert Chimeras Beitrag.
- **Falsche Baseline / Rosinenpicken** — schwach+Chimera mit *demselben schwachen Modell allein*
  vergleichen, auf den *identischen* Task-IDs, mit Seeds und vollständigen Logs. Ein
  Frontier-Modell ist eine Obergrenze, kein Rivale.
