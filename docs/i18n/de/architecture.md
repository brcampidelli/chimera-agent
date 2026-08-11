---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Architektur

Dieses Dokument bildet die Codebasis auf das Design und die Forschung ab, auf der sie aufbaut.
Für das "Warum" siehe [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## Der Reasoning-Kern: LLM-Fusion

`chimera/fusion/`

Die Fusion-Engine führt eine Aufgabe durch ein **Panel** von Modellen, lässt einen **Judge**
eine strukturierte Analyse erstellen (Konsens / Widersprüche / partielle Abdeckung / einzigartige
Erkenntnisse / blinde Flecken) und lässt anschließend einen **Synthesizer** die finale Antwort
verfassen, verankert in dieser Analyse (`FusionEngine`). Sie implementiert das
`SupportsComplete`-Protokoll und ist damit überall dort ein direkt einsetzbares Reasoning-Backend,
wo ein Modell erwartet wird — auch innerhalb der Agent-Loop.

Ein **kostenbewusster Router** (`RoutedBackend` + `RoutingPolicy`) hält Fusion selektiv: Turns mit
Tool-Aufrufen gehen an ein einzelnes Modell (Fusion ruft keine Tools auf), und nur tiefe / hoch
riskante Reasoning-Turns werden fusioniert. Inspiriert von OpenRouter Fusion (der Gewinn kommt aus
dem *Synthese*-Schritt, nicht nur aus der Modellvielfalt) und AURORA-AI (adaptives Budget über
heterogene Modelle hinweg).

## Die Agent-Loop & Tier-2-Autonomie

`chimera/core/`

- `Agent` — eine minimale ReAct-/Tool-Calling-Loop mit einem **expliziten Transcript** (der Zustand
  lebt außerhalb des Modells). Hängt nur von `SupportsComplete` + einer `ToolRegistry` ab.
- `AutonomousAgent` — Tier 2: ownership-begrenzten **Spine**-Kontext zusammenstellen → **planen** →
  Snapshot → ausführen → **Manager-Review** (generate-vs-verify) → **verify-or-revert** → mit
  Feedback erneut versuchen, wobei jeder Versuch im Experience-Buffer festgehalten wird.
- `WorkspaceGuard` — Snapshot/Restore für Textdateien, der Mechanismus hinter verify-or-revert.
- `CommandVerifier` — "ausführbarer Beweis" (Exit 0 == Erfolg).

### Gegen die Degradation bei kontinuierlicher Evolution

Das offene Problem (laut *Agentic Software*, `2606.05608`): Die Performance fällt von >80 % bei
isolierten Aufgaben auf ~38 % bei kontinuierlicher Evolution — Kontext über lange Zeiträume und
Fehlerfortpflanzung. Chimeras Gegenmaßnahmen, jede in der Literatur verankert:

| Gegenmaßnahme | Wo | Grundlage |
|---|---|---|
| Zustand externalisieren (Transcript/Workspace, nicht LLM-Kontext) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Ownership-begrenzter Kontext (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Generate-vs-verify-Supervision | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verify-or-revert | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Experience-Buffer (Fehlschläge als Negativbeispiele) | `evolution/experience.py` | HORIZON `2606.28279` |
| Nachrichtenkonsolidierung in Teams | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark für kontinuierliche Evolution | `eval/continuous.py` | EvoClaw-Problemstellung |

## Gedächtnis & Selbst-Evolution

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — hierarchische Elemente (working / episodic / semantic / persona) mit
  `ADD / UPDATE / DELETE / NOOP` (`remember`) und `merge`-Deduplizierung (Memory-R1, `2606.14502`).
- **Skill Evolver** — `SkillEvolver` schlägt aus einem Erfolg einen wiederverwendbaren
  `LearnedSkill` vor, testet ihn und behält ihn nur, wenn er besteht (propose → test →
  keep/discard). Gelernte Skills sind **Prompt-Vorlagen, kein ausführbarer Code** — sicher,
  autonom zu erstellen, noch vor jeder Selbstmodifikation auf Code-Ebene. Die Verfeinerung
  verbessert eine Vorlage anhand ihrer Fehlschläge (VIBEMed `2606.15504`).
- **Selbst gelernte Crons** — `CronLearner` erkennt wiederkehrende Aufgaben und schlägt Crons vor
  (`created_by=agent`, **deaktiviert**, bis ein Mensch zustimmt).
- **Benchmark für kontinuierliche Evolution** — führt eine Kette von Aufgaben durch einen Solver
  und meldet die Degradation (Gesamt-Erfolgsquote, erste vs. zweite Hälfte, längste Serie).

## Governance & Sicherheit

`chimera/governance/`

Ein sich selbst verbessernder Trust-Kernel (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Ein lexikalisches `RuleSet`
  behandelt Bedrohungen mit festem Signaturmuster deterministisch; ein optionaler **semantischer
  Judge** behandelt Intention; destillierte Regeln machen es mit der Zeit günstiger. Invariante:
  **niemals eine harmlose Aktion hart blockieren**.
- `SkillValidator` / `ScheduleValidator` — die **eingeschränkte, statisch prüfbare
  Bearbeitungsfläche** für Selbstmodifikation (AutoMegaKernel `2606.09682`): unsichere Vorschläge
  werden abgelehnt, bevor sie je ausgeführt werden.
- `AuditLog` — append-only JSONL von Entscheidungen und Evolutionsänderungen.
- `GovernedTool` / `govern_registry` — kapselt jedes Tool, sodass seine Ausführung kontrolliert
  wird; fügt sich unverändert in die bestehende Agent-Loop ein (`chimera ... --guard`).

### Die Taint-Schicht (Eindämmung von Prompt-Injection)

Über dem Kernel angesiedelt — heuristisch, ehrlich und nie eine harte Grenze (das ist die Sandbox):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — ein Capability-Ledger pro Lauf.
  Ein Fetch kontaminiert seinen Inhalt; ein Schreib-/Ausführungsvorgang, der kontaminierten Inhalt
  konsumiert, **eskaliert zur Review** (`assess_action`). Nicht vertrauenswürdiger, abgerufener
  Inhalt wird **data-fenced** zurückgegeben, wobei Chat-Template-Steuertoken entfernt werden
  (`sanitize.py`), und dauerhafte Artefakte aus einem kontaminierten Lauf behalten eine `tainted`-
  Herkunftskennzeichnung, damit sich Gift nicht in ein "sauberes" Memory/Skill hineinwaschen kann.
- `AggregateMonitor` (`aggregate_monitor.py`) — ein Monitor eine Ebene höher: Anhand der
  Capability-Events jedes Sub-Agenten erkennt er **aufgeteilte Abläufe**, die ein Monitor pro
  Agent nicht sehen kann (Agent A ruft nicht vertrauenswürdigen Inhalt ab, Agent B führt ihn aus
  oder **exfiltriert** ihn).
- `check_drift` (`drift.py`) — eine `Spec` ausführbarer Anforderungen (`defines`/`contains`/
  `absent`/`command`), die sowohl als Ground Truth für `solve --verify` dient als auch als
  Autorität des Projekt-Orchestrators darüber, was "fertig" ist (unten). Negative Prüfungen
  schlagen bei Dateien, die sie nicht scannen können, sicherheitshalber fehl.
- `QuarantineTool` + adaptive Allowlist (`quarantine.py`, `allowlist.py`) — ein
  Dual-LLM-/CaMeL-Reader unter Quarantäne und eine taint-adaptive Tool-Allowlist, die sich
  verengt, sobald ein Lauf kontaminiert ist.

## Multi-Agent-Teams (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — Rollenspezialisierung (im CrewAI-Stil).
- `SequentialCrew` — Rollen in fester Reihenfolge, jede sieht die **konsolidierten** vorherigen
  Ausgaben und kann in den gemeinsamen Speicher schreiben.
- `SupervisorCrew` — Worker bearbeiten die Aufgabe parallel, die Ausgaben werden konsolidiert, und
  ein Supervisor synthetisiert (im CAPRA-Stil, `parallel_review`, `2606.18976`).
- `consolidate` — MOC-Nachrichtenverschmelzung hält den Team-Kontext schlank (`2606.02359`).

## Sich selbst entwickelndes Ökosystem (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — entwirft/baut/bewertet spezialisierte Agenten (Agenten, die Agenten bauen). Zwei
  Schutzmechanismen aus der Meta-Agent Challenge (`2606.04455`): **Tool-Isolation** (die Tools
  eines entworfenen Agenten werden auf eine erlaubte Liste gefiltert) und **Trennung sichtbarer/
  versteckter Tests** (sichtbar bestanden + versteckt durchgefallen ⇒ Reward-Hacking wird
  vermutet, nicht als Erfolg gutgeschrieben).
- `ChangeQueue` — steuert das *Tempo* von Änderungen (FIFO-Merge-Queue + Batch-Obergrenzen), nicht
  die Kopfzahl ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — zeichnet (Prompt, Antwort, Ergebnis) auf und exportiert **SFT-/
  DPO**-Datensätze. Tatsächliches Fine-Tuning ist **opt-in und extern** — Chimera sammelt, es
  trainiert nicht.

## Kostenökonomie & die Delegationshierarchie

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

Delegation lohnt sich nur, wenn sie günstiger ist, als die Arbeit inline zu erledigen, und die
Behauptung wird **gemessen, nicht nur aufgestellt**:

- `HierarchicalOrchestrator` — zerlegen → budgetierte Worker beauftragen → jedes Ergebnis
  verifizieren → synthetisieren. Read-artige Fan-outs werden delegiert; eine trivial kleine
  Teilaufgabe wird direkt vom vertrauenswürdigen Top-Modell inline beantwortet.
- `CascadeBackend` — schwach → Gate → mittel → Gate → Fusion, wobei nur dann eskaliert wird, wenn
  die Antwort einer Stufe an einem günstigen Akzeptanz-Gate scheitert. Das **Route-Log**
  protokolliert jeden Hop, sodass die Kosten die **Summe über alle versuchten Hops** sind, nicht
  nur der akzeptierte — Eskalationen werden bezahlt.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — eine harte Token-Obergrenze, durchgesetzt am
  Backend, pro Worker.
- `EnvelopeVerifier` — Schema → Akzeptanzkriterien → probabilistischer **Stichprobencheck** (prüft
  die Treue einer Zusammenfassung gegenüber dem Rohartefakt); eine durch einen fehlgeschlagenen
  Stichprobencheck ausgelöste Rückfrage wird erneut geprüft.
- **Delegations-Belege** (`receipts.py`) — jede Delegation protokolliert ihre gemessenen Token/
  Kosten **und das inline-Gegenszenario in derselben Zeile**, bepreist zum jeweiligen Modelltarif
  (unbekanntes Modell → `None`, nie erfunden). Auch der eigene Decompose-/Synthese-Overhead des
  Orchestrators wird gemessen, sodass `summarize_delegations` (`chimera delegations`) eine
  **auditierbare** Nettoersparnis meldet und `cascade-bench` die **Kostenverteilung** (p50/p95/
  p99) meldet, nicht nur den Mittelwert.

## Das Selbst-Evolutions-Schwungrad

`chimera/evolution/`

Das "Training", das nie Gewichte berührt — fitness-gesteuert, gradientenfrei und reversibel:

- `EvolutionContext` — der gemeinsame Zusammenbau (Experience, Trajectories, Memory,
  Auto-Evolver, Skill-Cards, Playbook), der Lernen zu einer Eigenschaft des gesamten Agenten-
  *Stacks* macht, nicht nur des `solve`-Befehls.
- Skill-Cards + **GEPA**-Verfeinerung, ACE-**Playbook**, und eine `SkillLifecyclePolicy`, die
  einen Skill anhand seiner **gemessenen** Nutzungs-/Erfolgsstatistik befördert/zurückstuft (ein
  neuer Skill wird `provisional` geboren).
- Das **Diff-Gate** — ein "hohler Erfolg" (Verifier bestanden, aber der Workspace-Diff ist leer)
  erzeugt weder einen Skill noch ein Memory-Item; das Schwungrad lernt nur aus Arbeit, die
  tatsächlich stattgefunden hat.
- Das **Transfer-Gate** (`eval/transfer.py`) — ein feinjustiertes Artefakt wird nur befördert,
  wenn es sich auch auf einem Holdout hält, als Schutz gegen negativen Transfer.
  `maturity.Scorecard.weakest()` ist das Ziel: Die Schleife zielt auf die schwächste Fähigkeit.
  Regressionen werden nur bei einem **statistisch signifikanten** Rückgang automatisch
  zurückgerollt (ein Konfidenzintervall, nie ein Einzelwert).

Jede Umstellung eines Defaults wird hinter einem **vorregistrierten** gepaarten A/B-Test (`bench/`)
freigeschaltet, der veröffentlicht wird, egal ob er gewinnt oder verliert — kein Neuwürfeln bis zur
Signifikanz.

## Projektautonomie (von Anfang bis Ende)

`chimera/orchestration/project.py`

`ProjectOrchestrator` führt ein ganzes Projekt gegen eine `Spec` aus: Aufgabengraph (ein
Kanban-DAG mit `depends_on`) → jede fertige Karte wird gelöst (mit dem obigen Evolutionskontext)
→ **gegen die Spec abgenommen** via `check_drift` (die einzige Autorität für "fertig") →
unerfüllte Anforderungen erzeugen die nächsten Karten, in einer Schleife, bis die Spec erfüllt ist
oder ein Budget-/Max-Iterationen-/Human-Checkpoint sie stoppt. Riskante Schritte (`risk: high` —
Deploy / Migration / Löschen) **pausieren für menschliche Freigabe**; der Lauf ist dauerhaft und
fortsetzbar.

## Übergreifend

- **Provider** (`providers/`) — ein providerunabhängiges Gateway über LiteLLM; Keys können in
  `.env` liegen und werden in die Umgebung exportiert, damit LiteLLM sie sieht.
- **Tools** (`tools/`) — native Primitive; Tool-Metadaten sind Instanzattribute, damit dynamisch
  erzeugte Tools (OpenAPI/MCP) funktionieren.
- **Integrationen** (`integrations/`) — MCP-Client (optionales `mcp`-Extra) + OpenAPI→Tool-
  Importer + Connector-Registry.
- **Scheduler** (`scheduler/`) — Crons + Event-SOPs; die Zeit wird für deterministische Tests
  injiziert.
- **Migration** (`migration/`) — importiert Konfiguration + Skills + **merged** Langzeitgedächtnis
  aus Hermes / OpenClaw, dedupliziert und nicht-destruktiv.

## Testphilosophie

Jedes Subsystem ist mit **Fake-Backends** unit-getestet — deterministisch, ohne Netzwerk, ohne
Keys. Befehle, die tatsächlich ein LLM aufrufen, werden per Smoke-Test auf ihren
No-Key-Fehlerpfad geprüft. Das Quality-Gate (`ruff` + `mypy --strict` + `pytest`) läuft in CI auf
Python 3.11 und 3.12.
