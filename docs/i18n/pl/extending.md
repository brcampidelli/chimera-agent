---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Rozszerzanie Chimery

Chimera jest zbudowana z myślą o rozszerzaniu. Ten przewodnik pokazuje cztery sposoby, by
nauczyć ją czegoś nowego — **narzędzia** (tool), **skilla**, **przepisu** (recipe) lub
**integracji zewnętrznej** — każdy z kompletnym przykładem gotowym do wklejenia. Nie potrzeba
głębokiej znajomości bazy kodu.

Nowy w projekcie? Przeczytaj [Przewodnik użytkowania](usage.md) i [Przegląd
architektury](architecture.md). Gotowy wysłać zmianę? Zobacz
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Chcę… | Dodać… | Gdzie |
|---|---|---|
| Dać agentowi nową **akcję** (wywołać API, wykonać obliczenie, dotknąć urządzenia) | **Tool** | `chimera/tools/` |
| Zapakować możliwą do ponownego użycia **procedurę/prompt**, którą agent może wykorzystywać i doskonalić | **Skill** | `chimera/skills/builtin/` |
| Zautomatyzować **wieloetapową rutynę** opisaną w pliku | **Recipe** (YAML workflow) | `examples/` |
| Podłączyć **istniejące narzędzie/serwer zewnętrzny** bez forkowania | **Serwer MCP** | [mcp.md](mcp.md) |

---

## 1. Dodaj narzędzie (tool)

**Tool** to pojedyncza akcja, którą może wykonać agent. Odziedzicz po `Tool`, ustaw trzy
atrybuty i zaimplementuj `run` — która **zawsze zwraca string** (nigdy nie rzuca wyjątku;
zgłaszaj problemy jako `"error: …"`). To cały kontrakt.

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

**Zarejestruj je**, żeby agent mógł z niego korzystać — dodaj jedną linię w `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Przetestuj je** (bez sieci — testy Chimery są hermetyczne; mockuj granicę):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Konwencje, które utrzymują narzędzie w stanie mergowalnym:**
- `run` zwraca **string** i nigdy nie rzuca wyjątku — owiń I/O w `try/except` i zwróć
  `"error: …"`.
- **Leniwie importuj** ciężkie zależności wewnątrz `run`, nie na górze modułu.
- Trzymaj sekrety poza kodem — czytaj je ze środowiska (zobacz `chimera/config.py`).
- Jeśli narzędzie czyta niezaufaną treść (strony internetowe, pliki), jest automatycznie
  **otoczone ogrodzeniem danych** (data-fenced) i **śledzone pod kątem skażenia** (taint-tracked)
  przy uruchomieniu pod `--taint`; nie wymyślaj tego na nowo (zobacz [security.md](security.md)).

---

## 2. Dodaj skill

**Skill** to możliwa do ponownego użycia procedura wspierana przez model — poziom "augmented
tool", który agent wyświetla wg trafności, a silnik ewolucji później doprecyzowuje. Odziedzicz
po `LLMSkill`, ustaw `name` / `description` / `version` i zwróć `SkillResult`.

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

Zarejestruj je w `chimera/skills/builtin/__init__.py` (podążaj za istniejącym tam wzorcem).
Skille niosą **metryki użycia** i **cykl życia** (provisional → active → retired) sterowany
*zmierzonym* sukcesem — zobacz [`chimera/evolution/`](../chimera/evolution) — więc skill, który
przestaje pomagać, jest degradowany automatycznie, nigdy na podstawie zgadywania.

> **Tool czy skill?** **Tool** *robi* coś deterministycznego (wywołanie API, edycję pliku).
> **Skill** to *procedura oparta na promptach*, która korzysta z modelu. Wybierz tool dla akcji
> z efektami ubocznymi; skill dla wielokrotnego rozumowania/generowania.

---

## 3. Dodaj przepis (recipe / workflow)

**Recipe** automatyzuje wieloetapową rutynę bez żadnego kodu — plik YAML, który agent uruchamia
przez `chimera workflow`. Świetne do zadań zaplanowanych. Zobacz prawdziwe przykłady w
[`examples/`](../examples) (triage e-maili, poranny brief, watchdog repozytorium).

Każdy krok `uses` (używa) możliwości stosu agenta (`run`, `solve`, `crew`, …); `when:
prev_succeeded` bramkuje krok na podstawie poprzedniego, a `repeat` + `until: success` ponawia
krok.

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

## 4. Podłącz narzędzie zewnętrzne (MCP)

Aby użyć narzędzia, które **już istnieje** — serwer bazy danych, integrację SaaS, cudzy zestaw
narzędzi — nie forkujesz Chimery. Skieruj ją na dowolny **serwer MCP**, a jego narzędzia pojawią
się obok natywnych. Pełny przewodnik + uruchamialny przykład: **[mcp.md](mcp.md)**. Chimera może
też *być* serwerem MCP (`chimera serve --mcp`), więc inne agenty mogą używać *jej* narzędzi.

---

## Zanim otworzysz PR

Uruchom tę samą bramkę, którą uruchamia CI — musi być zielona:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Dodaj test do wszystkiego, co dodajesz, zachowaj `run` zwracające stringi (tools) lub
`SkillResult` (skills), i opisz **co / dlaczego / jak przetestować** w PR. Uczciwe, zmierzone
zmiany to poprzeczka — jeśli zmiana rości sobie poprawę, pokaż liczbę. Dziękujemy, że czynisz
Chimerę lepszą. 🧬
