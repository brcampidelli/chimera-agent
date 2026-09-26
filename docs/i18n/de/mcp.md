---
source_sha256: 0b37c6a629b664aa240e9c6470ce524d074bd195b985dfb116d34c593901f615
---

# MCP-Server verbinden

MCP (Model Context Protocol) ist der Standardweg, um externe Tools an einen Agenten
anzuschließen — GitHub, Dateisysteme, Notion, Datenbanken und hunderte weitere Server sprechen
es. Chimera hat einen erstklassigen MCP-Client: Die Tools jedes Servers werden zu gewöhnlichen
Chimera-Tools, die in derselben Registry wie die eingebauten sitzen, kontrolliert von denselben
Allowlist-/Kernel-/Ledger-Schichten.

## Das Client-Extra installieren

Der MCP-Client liegt hinter einem optionalen Extra, damit der Kern schlank bleibt:

```bash
uv sync --extra mcp
```

Die meisten Server sind Node-Pakete, daher wird auch `npx` benötigt (kommt mit Node.js).

## 60-Sekunden-Smoke-Test (ohne Zugangsdaten)

Der Referenz-Dateisystemserver braucht keine Token — er stellt nur Lese-/Schreib-Tools über ein
von dir gewähltes Verzeichnis bereit:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Diese Registry an einen `Agent` übergeben (oder `examples/mcp_github.py` für die vollständige
Loop ansehen), und das Modell kann jetzt die Tools des Servers wie jedes andere aufrufen.

## Ein echter Server: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

Das ist die ganze Integration: ~26 GitHub-Tools (Repos durchsuchen, Dateien lesen, Issues
auflisten, PRs erstellen, …) erscheinen in der Registry. Lauffähige End-to-End-Version:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Wie es zu den Sicherheitsschichten passt

MCP-Tools sind gewöhnliche `Tool`-Objekte, sodass sich alles zusammensetzt:

- **Allowlist pro Sitzung** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  gewährt nur die MCP-Tools, die dieser Lauf braucht; nicht gewährte erreichen das Modell nie.
- **Governance-Kernel** — `govern_registry(...)` kontrolliert MCP-Aufrufe mit allow/warn/
  review/block, genau wie jeden Shell-Befehl.
- **Taint-Ledger** — mit `ledger_registry(...)` umhüllen, und MCP-Fetches werden erfasst; zu
  beachten ist, dass heute nur die in `FETCH_TOOLS` genannten Tools automatisch klassifiziert
  werden, MCP-Inhalte also als nicht vertrauenswürdig behandelt und bevorzugt mit der Semantik
  `--taint --guard` ausgeführt werden sollten, wenn der Server externe Daten zieht.

## Chimera *als* MCP-Server

Der obige Client lässt Chimera andere Tools aufrufen. Der umgekehrte Weg funktioniert auch:
Chimera **als** MCP-Server laufen lassen, sodass jeder MCP-Client — Claude Desktop, eine IDE,
ein anderer Agent — die gesamte Engine als drei Tools aufrufen kann.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Es stellt bereit:

| Tool | Was es tut |
| --- | --- |
| `chimera_solve` | Löst eine Aufgabe autonom mit Plan + verify-or-revert; gibt die Antwort zurück. |
| `chimera_fuse` | Beantwortet einen Prompt über die LLM-Fusion-Engine (Panel → Judge → Synthesizer). |
| `chimera_memory_search` | Durchsucht Chimeras Langzeitgedächtnis und gibt die wichtigsten Fakten zurück. |

Einen MCP-Client als Stdio-Server darauf verweisen. Für Claude Desktop zur Konfiguration
hinzufügen:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` braucht einen Provider-Key für `chimera_solve`/`chimera_fuse` (die Speichersuche
funktioniert ohne). `--fuse` hinzufügen, um die tiefen Turns des Solvers über Fusion zu leiten,
`--no-memory`, um den Recall zu überspringen. Da Stdio die Leitung ist, gehen alle Logs an
stderr — stdout trägt nur das Protokoll.

## Claude die Desktop-App bedienen lassen

`chimera serve --mcp` baut einen eigenen Agenten. `chimera mcp desktop` baut nichts: Es ist eine
Fernbedienung für die Desktop-App, **die du schon offen hast**, also sieht Claude dieselben
Unterhaltungen, Läufe und Freigaben wie du, und was es startet, läuft unter der Governance der
App, auf ihren Bildschirmen.

1. Öffne in der App **Einstellungen → Claude** und schalte **Claude diese App bedienen lassen** ein.
2. Registriere den Server in Claude Code (oder trage denselben Befehl in die Config von Claude
   Desktop ein):

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

Mit dem ersten Schalter kann Claude Unterhaltungen lesen und starten (`desktop_send`), ebenso
Läufe, Batches, Boards und Cron-Jobs, das Gedächtnis durchsuchen und bearbeiten sowie Dateien und
den Git-Stand lesen. Ein Lauf, den es startet, trägt die Haltung, die du konfiguriert hast, und
eine Anfrage, die sie ausweiten will — ein `verify`-Befehl, Ausführung auf dem Host, ein anderer
Agent, Auto-Freigabe — wird abgelehnt. Freigaben bleiben bei dir: `desktop_approvals` listet sie
nur auf. Hält ein Zug bei einer an, antwortet `desktop_send` sofort, dass er auf dich wartet, und
der Zug läuft in der App weiter; `desktop_job` meldet, wie er endet.

Der zweite Schalter, **Volle Kontrolle**, fügt `desktop_approve` (Freigaben und Schritte mit
Schranke beantworten) und `desktop_settings` (Einstellungen, die Identität des Agenten und
gespeicherte Agenten bearbeiten, einen Befehl im Runner ausführen) hinzu. Damit kann Claude
Aktionen ohne dich freigeben — und eine Seite oder Nachricht mit Prompt-Injection, die der Agent
liest, könnte es dazu bringen. Diese beiden Tools werden gar nicht gelistet, solange er aus ist,
und die App lehnt sie ab, falls sie trotzdem aufgerufen werden.

Was kein Schalter erlaubt: einen API-Schlüssel, ein Token oder einen Webhook lesen oder
schreiben. Einstellungsänderungen lehnen Namen von Zugangsdaten ab, die Routen mit Schlüsseln
oder Freigabelinks sind nicht erreichbar, Dateien mit Zugangsdaten (`.env`, private Schlüssel)
lassen sich weder lesen noch schreiben noch durchsuchen, und jedes Ergebnis wird von Werten der
Zugangsdaten bereinigt. Auch ein Workspace auf dem Datenordner der App oder auf einem Ordner, der
ihn enthält (etwa deinem Home-Verzeichnis), wird abgelehnt: Dort liegen die Antworten auf
Freigaben, und eine dort geschriebene Datei würde eine beantworten.

Wie die Verbindung entsteht: Solange der Schalter an ist, schreibt die App
`~/.chimera/desktop-bridge.json` (die URL ihrer Loopback-API und ein zufälliges Token; unter
POSIX nur für dich lesbar, unter Windows in deinem Profil). Den Schalter auszuschalten oder die
App zu schließen löscht die Datei und macht das Token ungültig. Bei geschlossener App antwortet
jedes Tool „Chimera desktop is not running, or 'Allow Claude to operate this app' is off in
Settings.“ Claude listet die Tools beim Verbinden; nach dem Ein- oder Ausschalten von **Volle
Kontrolle** den Server neu verbinden (`/mcp` in Claude Code), um die neue Liste zu sehen.

## A2A sprechen (Agent → Agent)

MCP verbindet Agenten mit *Tools*; **A2A** (Agent2Agent, Linux Foundation) verbindet Agenten
*miteinander* — es ist nativ in LangGraph, CrewAI und AutoGen. Chimera spricht es ebenfalls,
sodass ein LangGraph-/CrewAI-Orchestrator eine Aufgabe an Chimera delegieren und ein
abgeschlossenes Ergebnis zurückbekommen kann.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` fügt dem HTTP-Server zwei Routen hinzu:

| Route | Zweck |
| --- | --- |
| `GET /.well-known/agent.json` | Die Agent Card — Identität + beworbene Skills (solve, fuse). |
| `POST /a2a` | JSON-RPC-2.0-Task-Lebenszyklus: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Ein Client sendet `message/send` mit einem Textteil; Chimera führt den autonomen Agenten aus
und gibt einen `completed`- (oder `failed`-)Task mit der Antwort als Agentennachricht zurück.
Oder er sendet `message/stream` und erhält einen **Server-Sent-Events**-Stream: zuerst den Task
im Zustand `working`, dann den Task `completed`/`failed`, sobald der Lauf beendet ist — sodass
ein Orchestrator Fortschritt sieht, ohne zu pollen. Die Agent Card bewirbt
`capabilities.streaming: true`.

**Umfang, ehrlich gesagt:** Der Stream sendet derzeit zwei Events (working → final), keine
Token-Deltas pro Schritt, und Push-Benachrichtigungen sind nicht implementiert. Das ist ein
konformer, poll-freier Stream — genug, um ein erstklassiger, streambarer Knoten in einer
LangGraph-/CrewAI-App zu sein.

## Fehlerbehebung

- `TimeoutError: MCP server ... did not become ready` — der Befehl ist nicht gestartet. Dieselbe
  `npx ...`-Zeile manuell in einem Terminal ausführen, um den Fehler zu sehen (fehlender Token,
  fehlendes Node, langsamer Paket-Download beim ersten Lauf — `connect_timeout` erhöhen).
- `ModuleNotFoundError: mcp` — das Extra installieren: `uv sync --extra mcp`.
- Namenskollisionen bei Tools — immer einen `name_prefix` übergeben.
- Die Sitzung führt den Server für die Lebensdauer deines Skripts als Subprozess aus; die
  `close()`-Methode der `connector`-Session aufrufen (oder den Prozess einfach beenden lassen),
  um ihn abzubauen.
