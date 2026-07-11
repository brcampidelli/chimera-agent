# Extending Chimera

Chimera is built to be extended. This guide shows the four ways to teach it something new —
a **tool**, a **skill**, a **recipe**, or an **external integration** — each with a complete,
copy-pasteable example. No deep knowledge of the codebase required.

New to the project first? Read the [Usage Guide](usage.md) and the
[Architecture overview](architecture.md). Ready to send a change? See
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| I want to… | Add a… | Where |
|---|---|---|
| Give the agent a new **action** (call an API, run a computation, touch a device) | **Tool** | `chimera/tools/` |
| Package a reusable **procedure/prompt** the agent can reuse and improve | **Skill** | `chimera/skills/builtin/` |
| Automate a **multi-step routine** described in a file | **Recipe** (workflow YAML) | `examples/` |
| Plug in an **existing external tool/server** without forking | **MCP server** | [mcp.md](mcp.md) |

---

## 1. Add a tool

A **tool** is a single action the agent can take. Subclass `Tool`, set three attributes, and
implement `run` — which **always returns a string** (never raises; report problems as
`"error: …"`). That's the whole contract.

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

**Register it** so the agent can use it — add one line in `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Test it** (no network — Chimera tests are hermetic; mock the boundary):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Conventions that keep a tool mergeable:**
- `run` returns a **string** and never raises — wrap I/O in `try/except` and return `"error: …"`.
- **Lazy-import** heavy dependencies inside `run`, not at module top.
- Keep secrets out of code — read them from the environment (see `chimera/config.py`).
- If the tool reads untrusted content (web pages, files), it's automatically **data-fenced** and
  **taint-tracked** when run under `--taint`; don't reinvent that (see [security.md](security.md)).

---

## 2. Add a skill

A **skill** is a reusable, model-backed procedure — the "augmented tool" tier the agent surfaces
by relevance and the evolution engine later refines. Subclass `LLMSkill`, set `name` /
`description` / `version`, and return a `SkillResult`.

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

Register it in `chimera/skills/builtin/__init__.py` (follow the existing pattern there). Skills
carry **usage metrics** and a **lifecycle** (provisional → active → retired) driven by *measured*
success — see [`chimera/evolution/`](../chimera/evolution) — so a skill that stops helping is
demoted automatically, never by guesswork.

> **Tool vs skill?** A **tool** *does* something deterministic (an API call, a file edit). A
> **skill** is a *prompted procedure* that uses the model. Pick a tool for actions with side
> effects; a skill for reusable reasoning/generation.

---

## 3. Add a recipe (workflow)

A **recipe** automates a multi-step routine without any code — a YAML file the agent runs with
`chimera workflow`. Great for scheduled jobs. See real ones in
[`examples/`](../examples) (email triage, morning brief, repo watchdog).

Each step `uses` a capability of the agent stack (`run`, `solve`, `crew`, …); `when:
prev_succeeded` gates a step on the previous one, and `repeat` + `until: success` retry a step.

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

## 4. Connect an external tool (MCP)

To use a tool that **already exists** — a database server, a SaaS integration, someone else's
toolkit — you don't fork Chimera. Point it at any **MCP server** and its tools appear alongside
the native ones. Full guide + a runnable example: **[mcp.md](mcp.md)**. Chimera can also *be* an
MCP server (`chimera serve --mcp`), so other agents can use *its* tools.

---

## Before you open a PR

Run the same gate CI runs — it must be green:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Add a test for anything you add, keep `run` returning strings (tools) or `SkillResult` (skills),
and describe **what / why / how to test** in the PR. Honest, measured changes are the bar — if a
change claims an improvement, show the number. Thank you for making Chimera better. 🧬
