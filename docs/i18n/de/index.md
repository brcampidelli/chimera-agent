---
source_sha256: 16ac522d3f1fdc1a508d268b9f88b22f75c896bd1ae6dd87839afd256e4d745f
---

# Chimera

Ein Open-Source-KI-Agent (Apache-2.0), der sich selbst weiterentwickelt und dessen Reasoning-
Kern **mehrere Modelle fusioniert** (Panel → Judge → Synthesizer) hinter einem kostenbewussten
Router — mit einem Governance-Kernel, einer Sandbox und einem Gedächtnis, das lernt.

Diese Seite ist aufgabenorientiert: wähle, was du tun möchtest.

<div class="grid cards" markdown>

- **:material-rocket-launch: Loslegen**
  Installieren, einen Key hinzufügen, die erste Aufgabe in fünf Minuten ausführen.
  [Installation & erster Lauf →](usage.md)

- **:material-toolbox: Etwas Reales tun**
  Lauffähige Recipes: E-Mail-Triage, ein täglicher Research-Brief, ein Repo-Watchdog.
  [Recipes →](recipes.md)

- **:material-power-plug: Tools verbinden**
  Jeden beliebigen MCP-Server einbinden (GitHub, Dateisystem, …).
  [MCP-Server →](mcp.md)

- **:material-account-switch: Einen anderen Agenten steuern**
  Einen Zug über ACP an Claude Code oder Gemini CLI übergeben — und nachlesen, was das mit
  den Garantien macht.
  [Externe Agenten →](external-agents.md)

- **:material-server: Betreiben**
  24/7 auf einem kleinen Server laufen lassen; Jobs planen; an Chat ausliefern.
  [Deploy →](deploy.md)

- **:material-shield-lock: Sicherheit**
  Governance, Sandbox, Taint-Tracking — und ihre ehrlichen Grenzen.
  [Sicherheit →](security.md) · [Sleeper-Channel-Audit →](../../audits/sleeper-channels.md)

- **:material-sitemap: Verstehen**
  Wie Fusion-Kern, Evolution und Sicherheitsschichten zusammenspielen.
  [Architektur →](architecture.md)

- **:material-console: Jeder Befehl**
  Alle 79, aus der CLI selbst erzeugt — darunter die 33, die in keiner Dokumentation standen.
  [Befehlsreferenz →](../../commands.md)

</div>

## Der Einzeiler

```bash
uv sync --extra dev && uv run chimera init
```

Dann `chimera run "..."` ausprobieren, oder ein echtes Recipe:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Standardmäßig ehrlich

Chimera ist **Alpha**. Es liefert Defense-in-Depth, aber die Dokumentation sagt unumwunden, wo
jede Schutzmaßnahme endet — die Injection-Verteidigung veröffentlicht sogar eine gemessene Zahl
(`chimera redteam`). Siehe [Sicherheit](security.md).
