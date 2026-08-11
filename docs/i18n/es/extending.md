---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Extender Chimera

Chimera está construido para ser extendido. Esta guía muestra las cuatro formas de enseñarle
algo nuevo — una **tool**, una **skill**, una **recipe**, o una **integración externa** — cada
una con un ejemplo completo y listo para copiar y pegar. No se requiere conocimiento profundo del
código base.

¿Eres nuevo en el proyecto? Lee la [Guía de uso](usage.md) y la
[Visión general de la arquitectura](architecture.md). ¿Listo para enviar un cambio? Consulta
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Quiero… | Agregar una(o)… | Dónde |
|---|---|---|
| Darle al agente una nueva **acción** (llamar una API, ejecutar un cálculo, tocar un dispositivo) | **Tool** | `chimera/tools/` |
| Empaquetar un **procedimiento/prompt** reutilizable que el agente pueda reusar y mejorar | **Skill** | `chimera/skills/builtin/` |
| Automatizar una **rutina de varios pasos** descrita en un archivo | **Recipe** (workflow YAML) | `examples/` |
| Conectar una **herramienta/servidor externo existente** sin bifurcar (fork) | **Servidor MCP** | [mcp.md](mcp.md) |

---

## 1. Agregar una tool

Una **tool** es una única acción que el agente puede realizar. Crea una subclase de `Tool`,
define tres atributos, e implementa `run` — que **siempre devuelve un string** (nunca lanza
excepción; reporta los problemas como `"error: …"`). Ese es todo el contrato.

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

**Regístrala** para que el agente pueda usarla — agrega una línea en `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Pruébala** (sin red — las pruebas de Chimera son herméticas; simula la frontera con un mock):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Convenciones que mantienen una tool fusionable:**
- `run` devuelve un **string** y nunca lanza excepción — envuelve el I/O en `try/except` y
  devuelve `"error: …"`.
- **Importa de forma perezosa** las dependencias pesadas dentro de `run`, no al inicio del
  módulo.
- Mantén los secretos fuera del código — léelos desde el entorno (consulta
  `chimera/config.py`).
- Si la tool lee contenido no confiable (páginas web, archivos), queda automáticamente
  **cercada como datos (data-fenced)** y con **seguimiento de taint** cuando se ejecuta bajo
  `--taint`; no reinventes eso (consulta [security.md](security.md)).

---

## 2. Agregar una skill

Una **skill** es un procedimiento reutilizable respaldado por el modelo — el nivel de "tool
aumentada" que el agente muestra por relevancia y que el motor de evolución refina más adelante.
Crea una subclase de `LLMSkill`, define `name` / `description` / `version`, y devuelve un
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

Regístrala en `chimera/skills/builtin/__init__.py` (sigue el patrón existente allí). Las skills
llevan **métricas de uso** y un **ciclo de vida** (provisional → active → retired) impulsado por
el éxito **medido** — consulta [`chimera/evolution/`](../chimera/evolution) — así que una skill
que deja de ayudar es degradada automáticamente, nunca por conjetura.

> **¿Tool o skill?** Una **tool** *hace* algo determinista (una llamada a API, una edición de
> archivo). Una **skill** es un *procedimiento con prompt* que usa el modelo. Elige una tool para
> acciones con efectos secundarios; una skill para razonamiento/generación reutilizable.

---

## 3. Agregar una recipe (workflow)

Una **recipe** automatiza una rutina de varios pasos sin ningún código — un archivo YAML que el
agente ejecuta con `chimera workflow`. Excelente para trabajos programados. Consulta ejemplos
reales en [`examples/`](../examples) (triaje de correo, resumen matutino, vigilante de repos).

Cada paso `uses` (usa) una capacidad del stack del agente (`run`, `solve`, `crew`, …); `when:
prev_succeeded` condiciona un paso al anterior, y `repeat` + `until: success` reintentan un paso.

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

## 4. Conectar una herramienta externa (MCP)

Para usar una herramienta que **ya existe** — un servidor de base de datos, una integración
SaaS, el toolkit de otra persona — no necesitas bifurcar (fork) Chimera. Apúntalo a cualquier
**servidor MCP** y sus herramientas aparecen junto a las nativas. Guía completa + un ejemplo
ejecutable: **[mcp.md](mcp.md)**. Chimera también puede *ser* un servidor MCP
(`chimera serve --mcp`), así otros agentes pueden usar *sus* herramientas.

---

## Antes de abrir un PR

Ejecuta el mismo gate que corre CI — debe estar en verde:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Agrega una prueba para todo lo que agregues, mantén `run` devolviendo strings (tools) o
`SkillResult` (skills), y describe **qué / por qué / cómo probarlo** en el PR. Cambios honestos y
medidos son el estándar — si un cambio afirma una mejora, muestra el número. Gracias por hacer
Chimera mejor. 🧬
