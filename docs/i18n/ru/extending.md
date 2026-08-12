---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Как расширить Chimera

Chimera построена так, чтобы её расширяли. Это руководство показывает четыре способа научить её
чему-то новому — **инструмент**, **навык**, **рецепт** или **внешнюю интеграцию** — и к каждому даёт
полный пример, готовый к копированию. Глубокого знания кодовой базы не требуется.

Впервые в проекте? Прочитайте [руководство по использованию](usage.md) и
[обзор архитектуры](architecture.md). Готовы прислать изменение? Смотрите
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Я хочу… | Добавить… | Где |
|---|---|---|
| Дать агенту новое **действие** (вызвать API, посчитать, обратиться к устройству) | **Инструмент** | `chimera/tools/` |
| Упаковать переиспользуемую **процедуру или промпт**, которую агент может применять и улучшать | **Навык** | `chimera/skills/builtin/` |
| Автоматизировать **многошаговую рутину**, описанную в файле | **Рецепт** (workflow YAML) | `examples/` |
| Подключить **существующий внешний инструмент или сервер** без форка | **Сервер MCP** | [mcp.md](mcp.md) |

---

## 1. Добавить инструмент

**Инструмент** — это одно действие, которое агент может совершить. Унаследуйтесь от `Tool`, задайте
три атрибута и реализуйте `run`, который **всегда возвращает строку** (никогда не поднимает
исключение; о проблемах сообщайте как `"error: …"`). Вот и весь договор.

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

**Зарегистрируйте его**, чтобы агент мог им пользоваться, — одна строка в `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Напишите тест** (без сети — тесты Chimera герметичны; подменяйте границу):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Договорённости, благодаря которым инструмент можно принять:**
- `run` возвращает **строку** и никогда не поднимает исключение — оборачивайте ввод-вывод в
  `try/except` и возвращайте `"error: …"`.
- **Импортируйте тяжёлые зависимости лениво**, внутри `run`, а не наверху модуля.
- Держите секреты вне кода — читайте их из окружения (смотрите `chimera/config.py`).
- Если инструмент читает недоверенное содержимое (веб-страницы, файлы), оно автоматически
  **обособляется как данные** и **отслеживается на заражение** при запуске с `--taint`; не
  изобретайте это заново (смотрите [security.md](security.md)).

---

## 2. Добавить навык

**Навык** — это переиспользуемая процедура, опирающаяся на модель: тот самый уровень «расширенного
инструмента», который агент поднимает по уместности, а движок эволюции позже дорабатывает.
Унаследуйтесь от `LLMSkill`, задайте `name`, `description` и `version` и верните `SkillResult`.

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

Зарегистрируйте его в `chimera/skills/builtin/__init__.py` (следуйте тамошнему образцу). Навыки несут
**статистику применения** и **жизненный цикл** (provisional → active → retired), которым движет
*измеренный* успех — смотрите [`chimera/evolution/`](../chimera/evolution), — поэтому навык,
переставший помогать, понижается автоматически, а не по чьей-то догадке.

> **Инструмент или навык?** **Инструмент** *делает* нечто детерминированное (вызов API, правка
> файла). **Навык** — это *процедура на промпте*, использующая модель. Берите инструмент для действий
> с побочными эффектами, а навык — для переиспользуемого рассуждения или порождения текста.

---

## 3. Добавить рецепт (сценарий)

**Рецепт** автоматизирует многошаговую рутину вовсе без кода — это файл YAML, который агент
выполняет командой `chimera workflow`. Отлично подходит для заданий по расписанию. Настоящие примеры
смотрите в [`examples/`](../examples) (разбор почты, утренняя сводка, наблюдение за репозиторием).

Каждый шаг через `uses` обращается к возможности агентского стека (`run`, `solve`, `crew`, …);
`when: prev_succeeded` ставит шаг в зависимость от предыдущего, а `repeat` вместе с `until: success`
повторяют шаг.

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

## 4. Подключить внешний инструмент (MCP)

Чтобы воспользоваться инструментом, который **уже существует** — сервером базы данных, интеграцией с
SaaS, чужим набором инструментов, — форкать Chimera не нужно. Направьте её на любой **сервер MCP**, и
его инструменты появятся рядом с родными. Полное руководство и готовый пример:
**[mcp.md](mcp.md)**. Chimera и сама может *быть* сервером MCP (`chimera serve --mcp`), чтобы другие
агенты пользовались *её* инструментами.

---

## Прежде чем открыть пул-реквест

Прогоните тот же заслон, что и CI, — он должен быть зелёным:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Добавьте тест ко всему, что добавляете, следите, чтобы `run` возвращал строки (инструменты) или
`SkillResult` (навыки), и опишите в пул-реквесте **что, зачем и как проверить**. Планка —
честные, измеренные изменения: если изменение заявляет улучшение, покажите число. Спасибо, что
делаете Chimera лучше. 🧬
