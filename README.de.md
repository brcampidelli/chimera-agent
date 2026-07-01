<div align="center">

<img src="assets/logo-wide.png" alt="Chimera Logo" width="460" />

# Chimera

**Ein quelloffener, sich selbst weiterentwickelnder KI-Agent, dessen Denk-Kern eine LLM-Fusion-Engine ist.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-beitreten-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <b>Deutsch</b> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera fusioniert **mehrere LLMs pro Anfrage** — eine **Panel → Richter → Synthesizer**-Pipeline,
inspiriert von OpenRouter Fusion — statt sich auf ein einzelnes Frontier-Modell zu verlassen, und
**verbessert sich im Laufe der Zeit** (Gedächtnis → Skills → Modell), während sie der
*Degradation durch kontinuierliche Evolution* widersteht, die heutige Agenten einschränkt.

> **Status:** frühe Entwicklung (0.1.x). Der vollständige Bauplan (M0–M7) ist umgesetzt —
> Tiers 1–4 + die Fusion-Engine + mehrstufige Selbstevolution + ein Governance-Kernel —
> plus eine **geschlossene Verhaltens-Lernschleife**, eine **operative Schicht** (Kanban +
> Worker-Lanes, SDLC-Crew, ein deklaratives Loop-DSL), **Ausführungsisolation** (Docker-
> Sandbox + git worktrees) und die **Paper-Techniken**, um die herum es entworfen wurde
> (HORIZON, VIBEMed, Spec Growth, AgentTrust v2, AutoMegaKernel, Meta-Agent, MOC).
> 332 Tests (+ opt-in Live-Integration) · `mypy --strict` sauber · `ruff` sauber.

---

## Warum Chimera

Bestehende Frameworks sind jeweils auf einer Achse stark: Hermes/OpenClaw entwickeln Skills weiter,
nutzen aber ein einzelnes Modell; CrewAI/LangGraph orchestrieren gut, lernen aber nicht;
TrustClaw/NemoClaw/ZeroClaw bringen Sicherheit/Sandboxing, entwickeln sich aber nicht weiter.
**Chimera vereint alle vier:**

- 🧬 **Fusion als Denken** — die Panel→Richter→Synthesizer-Engine ist der Denk-Kern, kein Add-on. Der Mehrwert entsteht durch den *Synthese*-Schritt selbst, nicht nur durch Modellvielfalt.
- 🪜 **Vier Fähigkeitsstufen in einer Progression** — erweiterte Werkzeuge → autonom für Einzelaufgaben → Multi-Agenten-Teams → selbstevolvierendes Ökosystem.
- ♻️ **Eine geschlossene, mehrstufige Selbstevolutionsschleife**, die der Degradation durch kontinuierliche Evolution gezielt entgegenwirkt (externalisierter Zustand, drift-resistenter Kontext, Verify-or-Revert, ein in die Planung zurückgeführter Erfahrungs-Buffer).
- 🛡️ **Ein Governance-Kernel, der sich ebenfalls verbessert** — allow/warn/block/review, mit statisch validierter Selbstmodifikations-Oberfläche und geschütztem Präzedenzfall.

## Funktionen

**Denken & Autonomie**
- **LLM-Fusion-Engine** — anbieterunabhängiges Panel aus Frontier- + offenen Modellen, ein Richter, der Konsens/Widersprüche/blinde Flecken aufzeigt, und ein Synthesizer; ein **kostenbewusster Router** fusioniert nur, wenn es sich lohnt (Tool-Schritte bleiben Einzelmodell).
- **Tier-2-Autonomie** — planen → ausführen → Manager-Review → **Verify-or-Revert** (Workspace-Snapshot/Restore + ein Befehls-Verifizierer), mit **git-worktree-Isolation** (`solve --isolate`) — Änderungen landen nur, wenn verifiziert.
- **SDLC-Lifecycle-Crew** (`chimera lifecycle`) — eine vorgefertigte **Plan → Build → Test → Review**-Pipeline mit Verify-or-Revert in der Test-Phase.
- **Multi-Agenten-Teams** — Rollenspezialisierung, sequentielle und Supervisor-Crews, MOC-Konsolidierung, gemeinsames Gedächtnis, parallele Review.

**Selbstevolution & Governance**
- **Geschlossene Verhaltensschleife** — vergangene Fehler speisen den Planner (Lektionen), verifizierte Erfolge schreiben automatisch ins Gedächtnis, und wiederkehrende Aufgaben entwickeln automatisch eine validierte, smoke-getestete Skill — alles per Verify-or-Revert gegated. Plus Continuous-Evolution-Benchmark und EvoClaw-Stresstest naive vs. guarded.
- **Hierarchisches Gedächtnis** — working / episodic / semantic / persona **+ eine Graph-Schicht** (`memory graph`), die Fakten nach Entität abruft, nicht nur nach Schlagwort.
- **Opt-in Modell-Evolution** — `solve` sammelt Trajektorien; `evolve` kuratiert SFT/DPO-Datasets und erzeugt ein lauffähiges LoRA-Rezept. Das Training bleibt extern/opt-in.
- **Governance-Kernel** — allow/warn/block/review (lexikalische Regeln + optionaler semantischer Richter, mit Regel-Distillation und einem **geschützten Präzedenz-Speicher**), ein statischer Validator für die Selbstmodifikations-Oberfläche, ein Append-only-Audit-Log, governte Tools, ein **Vier-Akteur-Änderungsmodell** und ein **Spec↔Code-Drift-Gate** (`chimera drift`).

**Anbieter**
- **Jedes Modell, eine Schnittstelle** — anbieterunabhängig via LiteLLM (100+ Modelle über `provider/model`-Slugs); First-Class-Keys für OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted & robust** — eigene Endpoints für **Ollama/vLLM** (`CHIMERA_API_BASE`), **Fallback-Ketten**, **Credential Pools** mit Round-Robin-Rotation, ein Live-**`/model`**-Wechsel und **Prompt-Caching** (`CHIMERA_CACHE`) für wiederholte Reasoning-Schritte.

**Orchestrierung, Schnittstellen & Integrationen**
- **Kanban + Worker-Lanes** (`chimera kanban`) — ein Task-Board (Backlog → Doing → Review → Done), dessen Karten an eine `solve`- oder `crew`-Lane verteilt werden; `kanban learn` macht wiederkehrende Aufgaben zu Karten.
- **Loop Engineering** (`chimera workflow`) — eine autonome Schleife als YAML verfassen (Schritte, die die Stack `nutzen`, mit `when`-Bedingungen und `repeat`/`until`-Schleifen).
- **Schnittstellen** — eine `chat`-REPL, eine Vollbild-**TUI** (Textual) und ein **Messaging-Gateway** (HTTP, oder **natives Discord/Telegram/Slack/Signal** via `serve --discord|--telegram|--slack|--signal`) mit einer Konversation (und Gedächtnis) pro Chat; der Agent kann Nachrichten auch über das `send_message`-Tool **senden** (WhatsApp zweiseitig via Webhook).
- **Ausführungs-Sandbox** — das Shell-Tool lokal oder in einem isolierten **Docker**-Container ausführen (`CHIMERA_SANDBOX=docker`).
- **Integrationen** — ein First-Class-**MCP**-Client (stdio) + ein **OpenAPI/REST → Tool**-Importer; **Crons** (von Menschen zugewiesen und selbstgelernt, mit Bestätigung); **Migration** von Config/Skills/Langzeitgedächtnis aus Hermes Agent / OpenClaw.

**Eingebaute Extras**
- **Vision** (Bild einfügen), **Deliverable-Modus** (polierte Artefakte) und ein **Pet**-Begleiter — plus voreingestellte Credential-Slots für Websuche, Bildgenerierung, TTS/Sprache und mehr (`chimera features`).

## Schnellstart

Benötigt Python **3.11+** (3.12+ empfohlen) und [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # mindestens einen Anbieter-Key setzen (OpenRouter empfohlen)
uv run chimera doctor       # Umgebung prüfen
```

## Befehle

```bash
chimera doctor / models / features    # Status, Konfiguration, optionale Fähigkeiten
chimera chat                          # interaktiver Mehrrunden-Assistent (deine rechte Hand)
chimera tui                           # Vollbild-Terminal-App (Textual)
chimera serve [--discord|--telegram|--slack|--signal]  # Messaging-Gateway: HTTP oder nativer Plattform-Bot
chimera run "PROMPT" --image pic.png   # Tier-1 Single-Shot (mit Vision via --image)
chimera deliver "ein Plan" -o plan.md   # Deliverable-Modus: erzeugt ein poliertes Artefakt
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: Panel -> Richter -> Synthesizer
chimera solve "AUFGABE" --verify "pytest -q" --rubric --isolate   # Tier-2: Verify-or-Revert (+ Kaskaden-Rubrik-Review), git-worktree-isoliert
chimera lifecycle "AUFGABE" --verify "..."   # SDLC-Crew: Plan -> Build -> Test -> Review
chimera workflow flow.yaml             # eine deklarative Schleife ausführen (Loop Engineering)
chimera crew "AUFGABE" --mode supervisor  # Tier-3 Multi-Agenten-Crew
chimera meta "ein Agent für X"          # Tier-4 Meta-Agent: entwirft einen spezialisierten Agenten
chimera kanban add/board/run/learn     # Task-Board mit Worker-Lanes (solve/crew)
chimera drift spec.yaml                # Spec<->Code-Drift-Gate (Exit 1 bei Drift)
chimera memory add / graph             # kuratiertes Langzeitgedächtnis + Entität-Relation-Graph
chimera cron add / learn               # geplante Jobs (zugewiesen + selbstgelernt, bestätigt)
chimera bench                          # Continuous-Evolution-Benchmark
chimera migrate hermes ~/.hermes --apply   # importiert Config + Skills + führt Gedächtnis zusammen
chimera evolve status / tune / recipe   # opt-in Evolution: Spec-Meta-Suche (tune), SFT/DPO-Daten + LoRA-Rezept
chimera pet new --name Chimi           # adoptiere einen virtuellen Begleiter
```

Siehe den **[Nutzungsleitfaden](docs/usage.md)** für Installation, Konfiguration und jeden Befehl mit Copy-Paste-Beispielen.

## Architektur

```
chimera/
  core/          Agenten-Schleife (ReAct) + Tier-2-Autonomie (Plan, Verify-or-Revert) + git-worktree-Isolation
  fusion/        Panel -> Richter -> Synthesizer + kostenbewusster Router
  memory/        working / episodic / semantic / persona + Graph-Schicht + Memory Manager
  skills/        eingebaute Bibliothek + Skill-Context-Retrieval
  evolution/     Skill-Evolver, Auto-Evolve-Hook, Erfahrungs-Buffer
  governance/    Trust-Kernel (Regeln + Richter + geschützter Präzedenzfall), statischer Validator, Drift-Gate, Vier-Akteur-Modell, Audit
  orchestration/ Rollen, sequentielle/Supervisor-Crews, MOC-Comms, SDLC-Lifecycle-Crew
  ecosystem/     Meta-Agent, Change-Tempo-Governance, Trajektorien-Sammlung, Modell-Evolution
  kanban/        Task-Board + Worker-Lanes (Verteilung an Crews / solve)
  workflow/      deklaratives Loop-DSL (Loop Engineering)
  tools/         native Tools (Dateien, Shell, HTTP)
  sandbox/       Ausführungs-Backends (local / docker-isoliert)
  integrations/  MCP-Client (stdio) + OpenAPI->Tool-Importer
  scheduler/     Crons (zugewiesen + selbstgelernt) + SOP-Engine
  migration/     Import aus Hermes/OpenClaw (Config, Skills, Gedächtnis-Merge)
  providers/     LLM-Gateway (LiteLLM) — Fallback, Credential Pools, eigene Endpoints, Prompt-Cache
  interface/     konversationelle ChatSession (geteilt von Chat, TUI, Gateway)
  tui/  server/   Vollbild-Textual-App · Messaging-Gateway + HTTP-Transport
  eval/          Continuous-Evolution + EvoClaw-Stresstest + tägliche Szenarien
  cli/           der `chimera`-Befehl (CLI-first)
```

Siehe [docs/architecture.md](docs/architecture.md) für das vollständige Design und die zugrunde liegende Forschung.

## Roadmap

| Meilenstein | Status |
|---|---|
| M0–M7 — Tiers 1–4 + Fusion + Selbstevolution + Governance | ✅ |
| M8 — Schnittstellen (Chat/TUI/Gateway), EvoClaw-Stresstest, opt-in Modell-Evolution | ✅ |
| Anbieter-Schicht — self-hosted Endpoints, Fallback, Credential Pools, `/model`, Prompt-Cache | ✅ |
| Geschlossene Verhaltensschleife — Erfahrung→Planner, Auto-Gedächtnis, Auto-Skill (governt) | ✅ |
| Operative Orchestrierung — Kanban + Worker-Lanes, SDLC-Crew, Loop-DSL | ✅ |
| Ausführungsisolation — Docker-Sandbox + git worktrees | ✅ |
| Paper-Techniken — HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| Paper-Techniken (II) — MemGate · multifaktorieller Speicherwert · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · OpenJarvis Spec-Suche | ✅ |

Als Nächstes: tiefere Continuous-Evolution-Validierung im großen Maßstab, Anbieter-OAuth-Logins und ein
optionales LangGraph-Durability-Backend. Modell-Training (LoRA/DPO) bleibt extern/opt-in by design.

## Entwicklung

```bash
uv run ruff check .      # Lint
uv run mypy chimera      # Typprüfung (strict)
uv run pytest -q         # Tests
```

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) und [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Sicherheitsprobleme: siehe [SECURITY.md](SECURITY.md).

## Community

Komm ins Gespräch auf **[Discord](https://discord.gg/ACvBbrmguV)** — Fragen, Ideen und Beiträge sind willkommen.

## Lizenz

[Apache-2.0](LICENSE).
