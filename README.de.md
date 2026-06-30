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
> Tiers 1–4 + die Fusion-Engine + Selbstevolution + ein Governance-Kernel — plus eine
> **Interface-Schicht** (Chat, TUI, HTTP-Gateway), **opt-in Modell-Evolution** und eine
> **Feature-Schicht** (Vision, Deliverable-Modus, Pets, …).
> 224 Tests (+ opt-in Live-Integration) · `mypy --strict` sauber · `ruff` sauber.

---

## Warum Chimera

Bestehende Frameworks sind jeweils auf einer Achse stark: Hermes/OpenClaw entwickeln Skills weiter,
nutzen aber ein einzelnes Modell; CrewAI/LangGraph orchestrieren gut, lernen aber nicht;
TrustClaw/NemoClaw/ZeroClaw bringen Sicherheit/Sandboxing, entwickeln sich aber nicht weiter.
**Chimera vereint alle vier:**

- 🧬 **Fusion als Denken** — die Panel→Richter→Synthesizer-Engine ist der Denk-Kern, kein Add-on. Der Mehrwert entsteht durch den *Synthese*-Schritt selbst, nicht nur durch Modellvielfalt.
- 🪜 **Vier Fähigkeitsstufen in einer Progression** — erweiterte Werkzeuge → autonom für Einzelaufgaben → Multi-Agenten-Teams → selbstevolvierendes Ökosystem.
- ♻️ **Mehrstufige Selbstevolution**, die der Degradation durch kontinuierliche Evolution gezielt entgegenwirkt (externalisierter Zustand, drift-resistenter Kontext, Verify-or-Revert, Erfahrungs-Buffer).
- 🛡️ **Ein Governance-Kernel, der sich ebenfalls verbessert** — allow/warn/block/review, mit einer statisch validierten Selbstmodifikations-Oberfläche.

## Funktionen

**Denken & Autonomie**
- **LLM-Fusion-Engine** — anbieterunabhängiges Panel aus Frontier- + offenen Modellen, ein Richter, der Konsens/Widersprüche/blinde Flecken aufzeigt, und ein Synthesizer; ein **kostenbewusster Router** fusioniert nur, wenn es sich lohnt (Tool-Schritte bleiben Einzelmodell).
- **Tier-2-Autonomie** — planen → ausführen → Manager-Review → **Verify-or-Revert** (Workspace-Snapshot/Restore + ein Befehls-Verifizierer), mit einem git-artigen Erfahrungs-Buffer.
- **Multi-Agenten-Teams** — Rollenspezialisierung, sequentielle und Supervisor-Crews, MOC-Nachrichtenkonsolidierung, gemeinsames Gedächtnis, parallele Review.

**Selbstevolution & Governance**
- **Selbstevolution** — ein Memory Manager (ADD/UPDATE/DELETE/NOOP-Dedup), ein Skill-Evolver, der *eigene Skills schreibt und testet* (vorschlagen → testen → behalten/verwerfen), selbstgelernte Crons und ein **Continuous-Evolution-Benchmark** (plus ein EvoClaw-Stresstest naive vs. guarded), der die Degradation misst.
- **Opt-in Modell-Evolution** — `solve` sammelt Trajektorien; `evolve` kuratiert sie zu SFT/DPO-Datasets und erzeugt ein lauffähiges LoRA-Rezept. Das Training bleibt **extern und opt-in** — niemals automatisch.
- **Governance & Sicherheit** — ein sich selbst verbessernder Trust-Kernel (allow/warn/block/review), ein statischer Validator für die Selbstmodifikations-Oberfläche, ein Append-only-Audit-Log und governte Tools.

**Anbieter**
- **Jedes Modell, eine Schnittstelle** — anbieterunabhängig via LiteLLM (100+ Modelle über `provider/model`-Slugs); First-Class-Keys für OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted & robust** — eigene Endpoints für **Ollama/vLLM** (`CHIMERA_API_BASE`), **Fallback-Ketten** über Modelle, **Credential Pools** mit Round-Robin-Key-Rotation und ein Live-**`/model`**-Wechsel in `chat`/`tui`.

**Schnittstellen & Integrationen**
- **CLI-first, plus Schnittstellen** — eine `chat`-REPL, eine Vollbild-**TUI** (Textual) und ein **Messaging-Gateway** als HTTP-Server mit einer Konversation (und Gedächtnis) pro Chat.
- **Integrationen** — ein First-Class-**MCP**-Client (stdio) + ein **OpenAPI/REST → Tool**-Importer, um jede Plattform oder API hinzuzufügen.
- **Crons & Proaktivität** — von Menschen zugewiesene und selbstgelernte geplante Aufgaben.
- **Migration** — importiert Config, Skills und **Langzeitgedächtnis** aus Hermes Agent / OpenClaw (das Gedächtnis wird *zusammengeführt*, nie überschrieben).

**Eingebaute Extras**
- **Vision** (Bild einfügen), **Deliverable-Modus** (erzeugt polierte, eigenständige Artefakte) und ein **Pet**-Begleiter — plus voreingestellte Credential-Slots für Websuche, Bildgenerierung, TTS/Sprache und mehr (`chimera features` zeigt, was bereit ist und was jeweils benötigt wird).

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
chimera serve                         # Messaging-Gateway-HTTP-Server (Sitzungen pro Chat)
chimera run "PROMPT" --image pic.png   # Tier-1 Single-Shot (mit Vision via --image)
chimera deliver "ein Launch-Plan" -o plan.md   # Deliverable-Modus: erzeugt ein poliertes Artefakt
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: Panel -> Richter -> Synthesizer
chimera agent "AUFGABE" --fuse --guard    # ReAct-Tool-Schleife (governte Tool-Aufrufe)
chimera solve "AUFGABE" --verify "pytest -q"   # Tier-2 autonom: planen -> Verify-or-Revert
chimera crew "AUFGABE" --mode supervisor  # Tier-3 Multi-Agenten-Crew
chimera meta "ein Agent für X"          # Tier-4 Meta-Agent: entwirft einen spezialisierten Agenten
chimera memory add "ein dauerhafter Fakt"    # kuratiertes Langzeitgedächtnis (dedupliziert)
chimera cron add NAME "0 9 * * *" "Bericht ausführen"   # eine Aufgabe planen
chimera cron learn                     # schlägt Crons aus wiederkehrenden Aufgaben vor (deaktiviert)
chimera bench                          # Continuous-Evolution-Benchmark
chimera guard "rm -rf /"               # Vorschau eines Governance-Urteils
chimera migrate hermes ~/.hermes --apply   # importiert Config + Skills + führt Gedächtnis zusammen
chimera evolve status / recipe             # opt-in Modell-Evolution: SFT/DPO-Daten + LoRA-Rezept
chimera pet new --name Chimi               # adoptiere einen virtuellen Begleiter (Stats verfallen mit der Zeit)
```

Siehe den **[Nutzungsleitfaden](docs/usage.md)** für Installation, Konfiguration und jeden Befehl mit Copy-Paste-Beispielen.

## Architektur

```
chimera/
  core/          Agenten-Schleife (ReAct) + Tier-2-Autonomie (Plan, Verify-or-Revert, Supervisor)
  fusion/        Panel -> Richter -> Synthesizer + kostenbewusster Router
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        eingebaute Bibliothek + Skill-Context-Retrieval
  evolution/     Evolver für gelernte Skills, Erfahrungs-Buffer
  governance/    Trust-Kernel (allow/warn/block/review), statischer Validator, Audit, governte Tools
  orchestration/ Rollen, sequentielle und Supervisor-Crews, MOC-Comms
  ecosystem/     Meta-Agent, Change-Tempo-Governance, Trajektorien-Sammlung, Modell-Evolution
  tools/         native Tools (Dateien, Shell, HTTP)
  integrations/  MCP-Client (stdio) + OpenAPI->Tool-Importer
  scheduler/     Crons (zugewiesen + selbstgelernt) + SOP-Engine
  migration/     Import aus Hermes/OpenClaw (Config, Skills, Gedächtnis-Merge)
  providers/     LLM-Gateway (LiteLLM) — Fallback-Ketten, Credential Pools, eigene Endpoints
  interface/     konversationelle ChatSession (geteilt von Chat, TUI, Gateway)
  tui/           Vollbild-Textual-App
  server/        Messaging-Gateway + HTTP-Transport (Sitzungen pro Chat)
  eval/          Continuous-Evolution + EvoClaw-Stresstest + tägliche Szenarien
  cli/           der `chimera`-Befehl (CLI-first)
```

Siehe [docs/architecture.md](docs/architecture.md) für das vollständige Design und die zugrunde liegende Forschung.

## Roadmap

| Meilenstein | Status |
|---|---|
| M0 — Grundlagen (Gateway, Config, CLI) | ✅ |
| M1 — Tier 1 + Tools/Skills/Integrationen/Crons/Migration | ✅ |
| M2 — LLM-Fusion-Engine + kostenbewusster Router | ✅ |
| M3 — Tier 2 autonom (Verify-or-Revert) | ✅ |
| M4 — Selbstevolution (Gedächtnis, Skills, gelernte Crons, Benchmark) | ✅ |
| M5 — Governance-Kernel | ✅ |
| M6 — Tier 3 Multi-Agenten-Teams | ✅ |
| M7 — Tier 4 selbstevolvierendes Ökosystem | ✅ |
| M8 — Schnittstellen (Chat/TUI/Gateway), EvoClaw-Stresstest, opt-in Modell-Evolution | ✅ |
| Anbieter-Schicht — self-hosted Endpoints, Fallback-Ketten, Credential Pools, `/model` | ✅ |
| Funktionen — Vision, Deliverable-Modus, Pets + voreingestellte Fähigkeits-Slots | ✅ |

Nach M7 wurde der Agent gegen reale Anbieter-Modelle gehärtet (live getestet: Fusion, Tier-2-`solve`,
die tägliche Szenario-Suite, das HTTP-Gateway, der OpenAPI-Importer und der stdio-MCP-Client).
Als Nächstes: tiefere Continuous-Evolution-Validierung im großen Maßstab, mehr Anbieter-Integrationen
(OAuth-Logins, Credential-Pool-Tuning) und ein optionales LangGraph-Durability-Backend.

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
