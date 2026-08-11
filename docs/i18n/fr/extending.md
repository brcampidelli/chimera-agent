---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Étendre Chimera

Chimera est conçu pour être étendu. Ce guide montre les quatre façons de lui apprendre quelque
chose de nouveau — un **tool**, une **skill**, une **recipe**, ou une **intégration externe** —
chacune avec un exemple complet, prêt à copier-coller. Aucune connaissance approfondie du code
n'est requise.

Nouveau sur le projet ? Lisez d'abord le [Guide d'utilisation](usage.md) et la
[vue d'ensemble de l'architecture](architecture.md). Prêt à envoyer une modification ? Voir
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Je veux… | Ajouter un(e)… | Où |
|---|---|---|
| Donner à l'agent une nouvelle **action** (appeler une API, exécuter un calcul, toucher un appareil) | **Tool** | `chimera/tools/` |
| Empaqueter une **procédure/prompt** réutilisable que l'agent peut réutiliser et améliorer | **Skill** | `chimera/skills/builtin/` |
| Automatiser une **routine à plusieurs étapes** décrite dans un fichier | **Recipe** (workflow YAML) | `examples/` |
| Brancher un **outil/serveur externe existant** sans forker | **Serveur MCP** | [mcp.md](mcp.md) |

---

## 1. Ajouter un tool

Un **tool** est une action unique que l'agent peut entreprendre. Sous-classez `Tool`, définissez
trois attributs, et implémentez `run` — qui **renvoie toujours une chaîne** (ne lève jamais
d'exception ; signalez les problèmes sous la forme `"error: …"`). C'est tout le contrat.

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

**Enregistrez-le** pour que l'agent puisse l'utiliser — ajoutez une ligne dans
`default_registry` (`chimera/tools/builtin.py`) :

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Testez-le** (pas de réseau — les tests de Chimera sont hermétiques ; simulez la frontière) :

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Conventions qui gardent un tool fusionnable (mergeable) :**
- `run` renvoie une **chaîne** et ne lève jamais d'exception — encapsulez les E/S dans un
  `try/except` et renvoyez `"error: …"`.
- **Importez de façon paresseuse (lazy)** les dépendances lourdes à l'intérieur de `run`, pas en
  haut du module.
- Gardez les secrets hors du code — lisez-les depuis l'environnement (voir `chimera/config.py`).
- Si le tool lit du contenu non fiable (pages web, fichiers), il est automatiquement **clôturé
  comme donnée (data-fenced)** et **suivi pour contamination (taint-tracked)** quand il tourne
  sous `--taint` ; ne réinventez pas ça (voir [security.md](security.md)).

---

## 2. Ajouter une skill

Une **skill** est une procédure réutilisable, adossée à un modèle — le palier « outil augmenté »
que l'agent fait remonter par pertinence et que le moteur d'évolution affine ensuite. Sous-classez
`LLMSkill`, définissez `name` / `description` / `version`, et renvoyez un `SkillResult`.

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

Enregistrez-la dans `chimera/skills/builtin/__init__.py` (suivez le pattern déjà présent
là-bas). Les skills portent des **métriques d'usage** et un **cycle de vie** (provisoire → actif
→ retiré) piloté par un succès *mesuré* — voir [`chimera/evolution/`](../chimera/evolution) —
donc une skill qui cesse d'aider est rétrogradée automatiquement, jamais par supposition.

> **Tool ou skill ?** Un **tool** *fait* quelque chose de déterministe (un appel d'API, une
> modification de fichier). Une **skill** est une *procédure guidée par prompt* qui utilise le
> modèle. Choisissez un tool pour les actions avec effets de bord ; une skill pour du
> raisonnement/de la génération réutilisable.

---

## 3. Ajouter une recipe (workflow)

Une **recipe** automatise une routine à plusieurs étapes sans aucun code — un fichier YAML que
l'agent exécute avec `chimera workflow`. Idéal pour les tâches planifiées. Voir des exemples
réels dans [`examples/`](../examples) (triage d'e-mails, brief matinal, surveillance de dépôt).

Chaque étape `uses` une capacité de la pile de l'agent (`run`, `solve`, `crew`, …) ; `when:
prev_succeeded` conditionne une étape à la précédente, et `repeat` + `until: success` relancent
une étape.

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

## 4. Connecter un outil externe (MCP)

Pour utiliser un outil qui **existe déjà** — un serveur de base de données, une intégration
SaaS, la boîte à outils de quelqu'un d'autre — vous ne forkez pas Chimera. Pointez-le vers
n'importe quel **serveur MCP** et ses outils apparaissent aux côtés des outils natifs. Guide
complet + exemple exécutable : **[mcp.md](mcp.md)**. Chimera peut aussi *être* un serveur MCP
(`chimera serve --mcp`), pour que d'autres agents puissent utiliser *ses* outils.

---

## Avant d'ouvrir une PR

Exécutez la même barrière que la CI — elle doit être verte :

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Ajoutez un test pour tout ce que vous ajoutez, gardez `run` qui renvoie des chaînes (tools) ou
`SkillResult` (skills), et décrivez **quoi / pourquoi / comment tester** dans la PR. La barre à
atteindre, ce sont des changements honnêtes et mesurés — si un changement prétend à une
amélioration, montrez le chiffre. Merci de rendre Chimera meilleur. 🧬
