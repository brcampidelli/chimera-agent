---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Chimera erweitern

Chimera ist darauf ausgelegt, erweitert zu werden. Diese Anleitung zeigt die vier Wege, ihm etwas
Neues beizubringen — ein **Tool**, einen **Skill**, ein **Recipe** oder eine **externe
Integration** — jeweils mit einem vollständigen, copy-paste-fähigen Beispiel. Keine tiefen
Kenntnisse der Codebasis nötig.

Neu im Projekt? Zuerst den [Nutzungsleitfaden](usage.md) und den
[Architektur-Überblick](architecture.md) lesen. Bereit, eine Änderung einzureichen? Siehe
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Ich möchte … | Ein/eine … hinzufügen | Wo |
|---|---|---|
| dem Agenten eine neue **Aktion** geben (eine API aufrufen, eine Berechnung ausführen, ein Gerät ansteuern) | **Tool** | `chimera/tools/` |
| eine wiederverwendbare **Prozedur/einen Prompt** verpacken, den der Agent wiederverwenden und verbessern kann | **Skill** | `chimera/skills/builtin/` |
| eine **mehrstufige Routine** automatisieren, die in einer Datei beschrieben ist | **Recipe** (Workflow-YAML) | `examples/` |
| ein **bestehendes externes Tool/einen Server** einbinden, ohne zu forken | **MCP-Server** | [mcp.md](mcp.md) |

---

## 1. Ein Tool hinzufügen

Ein **Tool** ist eine einzelne Aktion, die der Agent ausführen kann. Von `Tool` erben, drei
Attribute setzen und `run` implementieren — das **gibt immer einen String zurück** (wirft nie
eine Exception; Probleme werden als `"error: …"` gemeldet). Das ist der ganze Vertrag.

```python
# chimera/tools/weather.py
from __future__ import annotations

from typing import Any

from chimera.tools.base import Tool


class WeatherTool(Tool):
    name = "weather"
    description = "Get the current temperature for a city (demo tool)."
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Lisbon'."},
        },
        "required": ["city"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy import: keep tool construction cheap

        city = str(kwargs["city"]).strip()
        if not city:
            return "error: 'city' is required"
        try:
            geo = httpx.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1}, timeout=15,
            ).json()
            if not geo.get("results"):
                return f"error: city not found: {city}"
            lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]
            wx = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "current": "temperature_2m"},
                timeout=15,
            ).json()
            return f"{city}: {wx['current']['temperature_2m']}°C"
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return f"error: weather lookup failed: {exc}"
```

**Registrieren**, damit der Agent es nutzen kann — eine Zeile in `default_registry`
(`chimera/tools/builtin.py`) hinzufügen:

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Testen** (kein Netzwerk — Chimera-Tests sind hermetisch; die Grenze wird gemockt):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Konventionen, die ein Tool mergefähig halten:**
- `run` gibt einen **String** zurück und wirft nie — I/O in `try/except` einpacken und
  `"error: …"` zurückgeben.
- Schwere Abhängigkeiten **lazy importieren**, innerhalb von `run`, nicht am Modulanfang.
- Secrets aus dem Code heraushalten — sie aus der Umgebung lesen (siehe `chimera/config.py`).
- Liest das Tool nicht vertrauenswürdigen Inhalt (Webseiten, Dateien), wird er unter
  `--taint` automatisch **data-fenced** und **taint-getrackt**; das nicht neu erfinden (siehe
  [security.md](security.md)).

---

## 2. Einen Skill hinzufügen

Ein **Skill** ist eine wiederverwendbare, modellgestützte Prozedur — die "augmentierte
Tool"-Stufe, die der Agent nach Relevanz zutage fördert und die die Evolution-Engine später
verfeinert. Von `LLMSkill` erben, `name` / `description` / `version` setzen und ein
`SkillResult` zurückgeben.

```python
# chimera/skills/builtin/naming_skills.py
from __future__ import annotations

from typing import Any

from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill


class NameThingSkill(LLMSkill):
    """Suggest short, memorable names for a project or product."""

    name = "name_thing"
    description = "Suggest 5 short, brandable names for a described project."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        about = kwargs.get("about")
        if not isinstance(about, str) or not about.strip():
            return SkillResult(ok=False, error="missing required string 'about'")
        system = "You name things. Reply with exactly 5 short names, one per line, no numbering."
        text = self.ask(system=system, user=about)   # LLMSkill helper: one model call
        return SkillResult(ok=True, output=text)
```

In `chimera/skills/builtin/__init__.py` registrieren (dem dort vorhandenen Muster folgen).
Skills tragen **Nutzungsmetriken** und einen **Lebenszyklus** (provisional → active → retired),
gesteuert durch *gemessenen* Erfolg — siehe [`chimera/evolution/`](../chimera/evolution) — sodass
ein Skill, der aufhört zu helfen, automatisch zurückgestuft wird, nie aufgrund von Vermutungen.

> **Tool oder Skill?** Ein **Tool** *tut* etwas Deterministisches (ein API-Aufruf, eine
> Dateibearbeitung). Ein **Skill** ist eine *promptgesteuerte Prozedur*, die das Modell nutzt.
> Ein Tool für Aktionen mit Seiteneffekten wählen; einen Skill für wiederverwendbares
> Reasoning/Generieren.

---

## 3. Ein Recipe (Workflow) hinzufügen

Ein **Recipe** automatisiert eine mehrstufige Routine ganz ohne Code — eine YAML-Datei, die der
Agent mit `chimera workflow` ausführt. Ideal für geplante Jobs. Echte Beispiele in
[`examples/`](../examples) (E-Mail-Triage, Morning Brief, Repo-Watchdog).

Jeder Schritt `uses` eine Fähigkeit des Agenten-Stacks (`run`, `solve`, `crew`, …); `when:
prev_succeeded` bindet einen Schritt an den vorherigen, und `repeat` + `until: success`
wiederholen einen Schritt.

```yaml
# my_flow.yaml — build a small module, then write a changelog only if it built
name: build-and-report
steps:
  - name: build
    uses: solve
    with:
      task: "Create greeting.py with greet(name) returning 'Hello, ' + name."
      verify: "python -c \"import greeting; assert greeting.greet('x') == 'Hello, x'\""
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with:
      prompt: "Write a one-line changelog entry for adding greeting.greet()."
```

```bash
chimera workflow my_flow.yaml
# schedule it to run every morning at 8:
chimera cron add digest "0 8 * * *" "chimera workflow my_flow.yaml"
```

---

## 4. Ein externes Tool verbinden (MCP)

Um ein Tool zu nutzen, das **bereits existiert** — ein Datenbankserver, eine SaaS-Integration,
das Toolkit einer anderen Person — muss Chimera nicht geforkt werden. Es wird auf einen
beliebigen **MCP-Server** verwiesen, und dessen Tools erscheinen neben den nativen. Vollständige
Anleitung + ein lauffähiges Beispiel: **[mcp.md](mcp.md)**. Chimera kann auch selbst ein
MCP-Server *sein* (`chimera serve --mcp`), sodass andere Agenten *seine* Tools nutzen können.

---

## Bevor du einen PR öffnest

Denselben Gate-Check ausführen, den auch CI ausführt — er muss grün sein:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Für alles Hinzugefügte einen Test schreiben, `run` weiterhin Strings (Tools) bzw.
`SkillResult` (Skills) zurückgeben lassen und im PR **was / warum / wie zu testen** beschreiben.
Ehrliche, gemessene Änderungen sind der Maßstab — behauptet eine Änderung eine Verbesserung,
die Zahl zeigen. Danke, dass du Chimera besser machst. 🧬
