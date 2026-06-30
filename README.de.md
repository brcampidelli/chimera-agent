<div align="center">

<img src="assets/logo-wide.png" alt="Chimera Logo" width="460" />

# Chimera

**Ein quelloffener, selbstentwickelnder KI-Agent, dessen Denkkern eine LLM-Fusion-Engine ist.**

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
**verbessert sich mit der Zeit selbst** (Gedächtnis → Skills → Modell). Damit widersteht es der
*kontinuierlichen Evolutionsdegradation*, die heutige Agenten begrenzt.

> **Status:** frühe Alpha. Alle 8 Meilensteine des Bauplans (M0–M7) sind umgesetzt:
> Tiers 1–4 + die Fusion-Engine + Selbstentwicklung + ein Governance-Kernel.
> 158 Tests · `mypy --strict` sauber · `ruff` sauber.

---

## Warum Chimera

Bestehende Frameworks sind jeweils auf **einer Achse** stark: Hermes/OpenClaw entwickeln Skills,
laufen aber mit einem einzigen Modell; CrewAI/LangGraph orchestrieren gut, lernen aber nicht;
TrustClaw/NemoClaw/ZeroClaw bringen Sicherheit/Sandbox, entwickeln sich aber nicht.
**Chimera vereint alle vier:**

- 🧬 **Fusion als Denkkern** — die Panel→Richter→Synthesizer-Engine ist der Denkkern, kein Add-on. Der Gewinn entsteht durch die *Synthese* selbst, nicht nur durch Modellvielfalt.
- 🪜 **Vier Fähigkeitsstufen in einer Progression** — erweiterte Werkzeuge → autonom für Einzelaufgaben → Multi-Agenten-Teams → selbstentwickelndes Ökosystem.
- ♻️ **Mehrstufige Selbstentwicklung**, die der kontinuierlichen Evolutionsdegradation gezielt entgegenwirkt (externalisierter Zustand, drift-resistenter Kontext, Verify-or-Revert, Erfahrungspuffer).
- 🛡️ **Ein Governance-Kernel, der sich ebenfalls verbessert** — allow/warn/block/review, mit einer statisch validierten Selbstmodifikations-Oberfläche.

## Funktionen

- **LLM-Fusion-Engine** — anbieterunabhängiges Panel aus Frontier- + offenen Modellen, ein Richter, der Konsens/Widersprüche/blinde Flecken sichtbar macht, und ein Synthesizer; ein **kostenbewusster Router** fusioniert nur, wenn es sich lohnt (Werkzeug-Runden bleiben bei einem einzigen Modell).
- **Tier-2-Autonomie** — planen → ausführen → Manager-Prüfung → **Verify-or-Revert** (Workspace-Snapshot/Restore + Befehls-Verifier), mit einem git-artigen Erfahrungspuffer.
- **Selbstentwicklung** — ein Memory Manager (ADD/UPDATE/DELETE/NOOP-Dedup), ein Skill-Evolver, der *eigene Skills schreibt und testet* (vorschlagen → testen → behalten/verwerfen), selbst gelernte Crons und ein **Benchmark für kontinuierliche Evolution**, der die Degradation misst.
- **Multi-Agenten-Teams** — Rollenspezialisierung, sequentielle und Supervisor-Crews, MOC-Nachrichtenkonsolidierung, geteiltes Gedächtnis, parallele Prüfung.
- **Governance & Sicherheit** — ein selbstverbessernder Trust-Kernel, ein statischer Validator für die Selbstmodifikations-Oberfläche, ein Append-only-Audit-Log und kontrollierte Werkzeuge.
- **Integrationen** — erstklassiger **MCP**-Client + ein **OpenAPI/REST → Tool**-Importer, um jede Plattform oder API hinzuzufügen.
- **Crons & Proaktivität** — von Menschen zugewiesene und selbst gelernte geplante Aufgaben.
- **Migration** — importiere Konfiguration, Skills und **Langzeitgedächtnis** von Hermes Agent / OpenClaw (das Gedächtnis wird *zusammengeführt*, nie überschrieben).
- **CLI-first** — alles funktioniert im Terminal; anbieterunabhängig über LiteLLM/OpenRouter.

## Schnellstart

Erfordert Python **3.11+** (3.12+ empfohlen) und [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # mindestens einen Anbieter-Key setzen (OpenRouter empfohlen)
uv run chimera doctor       # Umgebung prüfen
```

## Befehle

```bash
chimera doctor / models               # Status & Konfiguration
chimera run "PROMPT"                   # einmaliger Tier-1-Completion
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: Panel -> Richter -> Synthesizer
chimera agent "TASK" --fuse --guard    # ReAct-Agenten-Loop (kontrollierte Tool-Calls)
chimera solve "TASK" --verify "pytest -q"   # Tier-2 autonom: planen -> Verify-or-Revert
chimera crew "TASK" --mode supervisor  # Tier-3 Multi-Agenten-Crew
chimera meta "an agent for X"          # Tier-4 Meta-Agent: entwirft einen spezialisierten Agenten
chimera memory add "ein dauerhafter Fakt"   # kuratiertes Langzeitgedächtnis (dedupliziert)
chimera cron add NAME "0 9 * * *" "run report"   # eine Aufgabe planen
chimera cron learn                     # Crons aus wiederkehrenden Aufgaben vorschlagen (deaktiviert)
chimera bench                          # Benchmark für kontinuierliche Evolution
chimera guard "rm -rf /"               # ein Governance-Urteil vorab ansehen
chimera migrate hermes ~/.hermes --apply   # Konfiguration + Skills importieren + Gedächtnis zusammenführen
```

## Architektur

```
chimera/
  core/          Agenten-Loop (ReAct) + Tier-2-Autonomie (Plan, Verify-or-Revert, Supervisor)
  fusion/        Panel -> Richter -> Synthesizer + kostenbewusster Router
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        eingebaute Bibliothek + Skill-Kontext-Abruf
  evolution/     Evolver für gelernte Skills, Erfahrungspuffer
  governance/    Trust-Kernel (allow/warn/block/review), statischer Validator, Audit, kontrollierte Tools
  orchestration/ Rollen, sequentielle & Supervisor-Crews, MOC-Kommunikation
  ecosystem/     Meta-Agent, Governance des Änderungstempos, Trajektorien-Sammlung
  tools/         native Tools (Dateien, Shell, HTTP)
  integrations/  MCP-Client + OpenAPI->Tool-Importer
  scheduler/     Crons (zugewiesen + selbst gelernt) + SOP-Engine
  migration/     Import aus Hermes/OpenClaw (Konfig, Skills, Gedächtnis-Merge)
  providers/     LLM-Adapter (LiteLLM / OpenRouter)
  eval/          Benchmark für kontinuierliche Evolution, Demo-Aufgaben
  cli/           der `chimera`-Befehl (CLI-first)
```

Siehe [docs/architecture.md](docs/architecture.md) für das vollständige Design und die zugrunde liegende Forschung.

## Roadmap

| Meilenstein | Status |
|---|---|
| M0 — Grundlagen (Gateway, Konfig, CLI) | ✅ |
| M1 — Tier 1 + Tools/Skills/Integrationen/Crons/Migration | ✅ |
| M2 — LLM-Fusion-Engine + kostenbewusster Router | ✅ |
| M3 — Tier 2 autonom (Verify-or-Revert) | ✅ |
| M4 — Selbstentwicklung (Gedächtnis, Skills, gelernte Crons, Benchmark) | ✅ |
| M5 — Governance-Kernel | ✅ |
| M6 — Tier 3 Multi-Agenten-Teams | ✅ |
| M7 — Tier 4 selbstentwickelndes Ökosystem | ✅ |

Als Nächstes: Validierung mit echten Modellen im großen Maßstab, eine erweiterte Suite für kontinuierliche Evolution und ein optionales LangGraph-Durability-Backend.

## Entwicklung

```bash
uv run ruff check .      # Lint
uv run mypy chimera      # Typprüfung (strict)
uv run pytest -q         # Tests
```

Siehe [CONTRIBUTING.md](CONTRIBUTING.md) und [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Sicherheitsprobleme: siehe [SECURITY.md](SECURITY.md).

## Community

Mach mit auf **[Discord](https://discord.gg/ACvBbrmguV)** — Fragen, Ideen und Beiträge sind willkommen.

## Lizenz

[Apache-2.0](LICENSE).
