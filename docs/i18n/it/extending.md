---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Estendere Chimera

Chimera è costruito per essere esteso. Questa guida mostra i quattro modi per insegnargli
qualcosa di nuovo — un **tool**, una **skill**, una **recipe**, o un'**integrazione esterna** —
ciascuno con un esempio completo, pronto da copiare e incollare. Non serve una conoscenza
approfondita del codebase.

Nuovo al progetto? Leggi la [Guida all'uso](usage.md) e la
[panoramica sull'Architettura](architecture.md). Pronto a inviare una modifica? Vedi
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Voglio… | Aggiungere un… | Dove |
|---|---|---|
| Dare all'agente una nuova **azione** (chiamare un'API, eseguire un calcolo, toccare un dispositivo) | **Tool** | `chimera/tools/` |
| Impacchettare una **procedura/prompt** riutilizzabile che l'agente può riusare e migliorare | **Skill** | `chimera/skills/builtin/` |
| Automatizzare una **routine multi-passo** descritta in un file | **Recipe** (workflow YAML) | `examples/` |
| Collegare un **tool/server esterno già esistente** senza fare fork | **Server MCP** | [mcp.md](mcp.md) |

---

## 1. Aggiungere un tool

Un **tool** è una singola azione che l'agente può compiere. Fai una sottoclasse di `Tool`,
imposta tre attributi, e implementa `run` — che **restituisce sempre una stringa** (non solleva
mai eccezioni; riporta i problemi come `"error: …"`). Questo è l'intero contratto.

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

**Registralo** perché l'agente possa usarlo — aggiungi una riga in `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Testalo** (senza rete — i test di Chimera sono ermetici; fai il mock del confine):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Convenzioni che mantengono un tool mergeabile:**
- `run` restituisce una **stringa** e non solleva mai eccezioni — avvolgi l'I/O in `try/except`
  e restituisci `"error: …"`.
- Fai l'**import pigro** delle dipendenze pesanti dentro `run`, non in cima al modulo.
- Tieni i segreti fuori dal codice — leggili dall'ambiente (vedi `chimera/config.py`).
- Se il tool legge contenuto non fidato (pagine web, file), viene automaticamente
  **recintato come dato** e **tracciato per taint** quando eseguito sotto `--taint`; non
  reinventare questo (vedi [security.md](security.md)).

---

## 2. Aggiungere una skill

Una **skill** è una procedura riutilizzabile, sostenuta dal modello — il livello di "tool
potenziato" che l'agente mostra per rilevanza e che il motore di evoluzione poi affina. Fai una
sottoclasse di `LLMSkill`, imposta `name` / `description` / `version`, e restituisci un
`SkillResult`.

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

Registrala in `chimera/skills/builtin/__init__.py` (segui il pattern già esistente lì). Le
skill portano **metriche d'uso** e un **ciclo di vita** (provisional → active → retired) guidato
da un successo *misurato* — vedi [`chimera/evolution/`](../chimera/evolution) — così una skill
che smette di aiutare viene retrocessa automaticamente, mai per congettura.

> **Tool o skill?** Un **tool** *fa* qualcosa di deterministico (una chiamata API, una modifica
> di file). Una **skill** è una *procedura con prompt* che usa il modello. Scegli un tool per
> azioni con effetti collaterali; una skill per ragionamento/generazione riutilizzabile.

---

## 3. Aggiungere una recipe (workflow)

Una **recipe** automatizza una routine multi-passo senza alcun codice — un file YAML che
l'agente esegue con `chimera workflow`. Ottimo per job pianificati. Vedi esempi reali in
[`examples/`](../examples) (triage delle email, brief mattutino, watchdog di repository).

Ogni passo `uses` una capacità dello stack dell'agente (`run`, `solve`, `crew`, …); `when:
prev_succeeded` condiziona un passo al precedente, e `repeat` + `until: success` fanno riprovare
un passo.

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

## 4. Collegare un tool esterno (MCP)

Per usare un tool che **esiste già** — un server database, un'integrazione SaaS, il toolkit di
qualcun altro — non serve fare fork di Chimera. Puntalo verso qualsiasi **server MCP** e i suoi
tool compaiono accanto a quelli nativi. Guida completa + un esempio eseguibile:
**[mcp.md](mcp.md)**. Chimera può anche *essere* un server MCP (`chimera serve --mcp`), così
altri agenti possono usare *i suoi* tool.

---

## Prima di aprire una PR

Esegui lo stesso gate che esegue la CI — deve essere verde:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Aggiungi un test per tutto ciò che aggiungi, mantieni `run` che restituisce stringhe (tool) o
`SkillResult` (skill), e descrivi **cosa / perché / come testare** nella PR. Modifiche oneste e
misurate sono l'asticella — se una modifica dichiara un miglioramento, mostra il numero. Grazie
per rendere Chimera migliore. 🧬
